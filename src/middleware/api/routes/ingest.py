from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from middleware.ai.yaml_generator import (
    diagnose_yaml_with_ai,
    generate_yaml_from_excel,
    refine_yaml_with_feedback,
)
from middleware.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

PENDING_DIR = Path("/app/uploads/pending")
N8N_WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL", "http://n8n:5679/webhook/pending-config")
API_BASE_URL = os.environ.get("MIDDLEWARE_API_URL", "http://localhost:8000")


class UnknownIngestRequest(BaseModel):
    filename: str
    folder_name: str
    file_path: str
    pending_id: str | None = None
    web_url: str | None = None  # lien SharePoint pour ouvrir le fichier en 1 clic


class UnknownIngestResponse(BaseModel):
    pending_id: str
    supplier_guess: str
    message: str


def _find_pending_for_file(folder_name: str, filename: str) -> dict | None:
    """Retourne une demande déjà 'pending' pour ce (dossier, fichier), sinon None."""
    if not PENDING_DIR.exists():
        return None
    for meta_path in PENDING_DIR.glob("*.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (
            data.get("status") in ("pending", "processing")
            and data.get("folder_name") == folder_name
            and data.get("filename") == filename
        ):
            return data
    return None


_CONFIDENCE_TARGET = 90  # score minimum pour considérer le YAML prêt
_MAX_ITERATIONS = 3      # nombre max de tours (1 génération + 2 corrections)


def _python_parse_and_gery(file_path: Path, yaml_content: str) -> tuple[dict, list[dict]]:
    """Parse le fichier avec le YAML et génère l'aperçu Gery. Retourne (parse_results, gery_rows)."""
    from middleware.delta.engine import compute_delta
    from middleware.exporter.gery import build_new_article_rows
    from middleware.parser.yaml_loader import validate_mapping_yaml
    from middleware.pipeline import parse_with_rule
    from middleware.sage_codes import resolve_sage_code

    rule, erreurs = validate_mapping_yaml(yaml_content)
    if erreurs or rule is None:
        return {"yaml_valid": False, "parse_ok": False, "errors": erreurs}, []

    try:
        result = parse_with_rule(file_path, rule)
        products = result.products
        total = len(products)
        sans_prix = sum(1 for p in products if not p.prices and not p.variants)
        avec_prix = total - sans_prix
        warnings = []
        if not products:
            warnings.append("Aucun produit extrait")
        if sans_prix:
            warnings.append(f"{sans_prix} produit(s) sans prix")
        if result.error_count:
            warnings.append(f"{result.error_count} ligne(s) en erreur")

        parse_results = {
            "yaml_valid": True, "parse_ok": True, "errors": [],
            "total_produits": total, "avec_prix": avec_prix,
            "sans_prix": sans_prix, "erreurs_parsing": result.error_count,
            "warnings": warnings,
            "sample": [
                {
                    "code": p.supplier_product_code,
                    "designation": p.designation,
                    "prix": float(p.prices[0].amount) if p.prices else (
                        float(p.variants[0].prices[0].amount)
                        if p.variants and p.variants[0].prices else None
                    ),
                    "ligne": p.source_row,
                }
                for p in products[:5]
            ],
        }

        delta = compute_delta(products, known_hashes={})
        gery_rows = build_new_article_rows(
            delta, rule.gery_export,
            result.file_metadata.validity_start,
            result.file_metadata.validity_end,
            resolve_sage_code(rule.supplier_code),
        )
        return parse_results, gery_rows

    except Exception as exc:
        return {"yaml_valid": True, "parse_ok": False, "fatal": str(exc)}, []


def _run_refinement_loop(
    file_path: Path,
    folder_name: str,
    filename: str,
) -> tuple[str, str, str, dict, list[dict]]:
    """Boucle agentique : Agent1 génère → Agent2 juge → Agent1 corrige → ...

    Jusqu'à _CONFIDENCE_TARGET % ou _MAX_ITERATIONS tours.

    Returns:
        (supplier_code, yaml_final, initial_prompt, diagnostic_final, history)
    """
    # Tour 0 : génération initiale (Agent 1)
    try:
        supplier_code, yaml_content, initial_prompt = generate_yaml_from_excel(
            file_path=file_path,
            folder_name=folder_name,
            filename=filename,
        )
    except Exception as exc:
        supplier_code = folder_name.lower().replace(" ", "_")
        yaml_content = f'# Génération échouée : {exc}\nsupplier_code: "{supplier_code}"\n'
        return supplier_code, yaml_content, str(exc), {}, []

    history: list[dict] = []
    best_yaml = yaml_content
    best_confidence = 0

    for iteration in range(_MAX_ITERATIONS):
        # Parsing Python
        parse_results, gery_rows = _python_parse_and_gery(file_path, yaml_content)

        # Agent 2 : juge
        diagnosis = diagnose_yaml_with_ai(yaml_content, file_path, parse_results, gery_rows)
        confidence = diagnosis.get("confidence", 0)

        history.append({
            "iteration": iteration + 1,
            "confidence": confidence,
            "verdict": diagnosis.get("verdict", ""),
            "resume": diagnosis.get("resume", ""),
            "issues": diagnosis.get("issues", []),
        })

        logger.info(
            "tour de raffinage",
            iteration=iteration + 1,
            confidence=confidence,
            verdict=diagnosis.get("verdict"),
            fichier=filename,
        )

        if confidence > best_confidence:
            best_confidence = confidence
            best_yaml = yaml_content

        if confidence >= _CONFIDENCE_TARGET:
            break

        if iteration < _MAX_ITERATIONS - 1:
            # Agent 1 : correction
            try:
                _, yaml_content = refine_yaml_with_feedback(
                    yaml_content,
                    file_path,
                    diagnosis.get("issues", []),
                    diagnosis.get("suggestions", []),
                )
            except Exception as exc:
                logger.warning("raffinage Agent1 échoué", erreur=str(exc))
                break

    # Reparse le best_yaml pour le diagnostic final
    final_parse, final_gery = _python_parse_and_gery(file_path, best_yaml)
    final_diagnosis = diagnose_yaml_with_ai(best_yaml, file_path, final_parse, final_gery)
    final_diagnosis["iterations"] = len(history)
    final_diagnosis["history"] = history

    return supplier_code, best_yaml, initial_prompt, final_diagnosis, history


def _refine_in_background(pending_id: str, file_path: Path, folder_name: str, filename: str) -> None:
    """Lance la boucle agentique en tâche de fond et met à jour le pending une fois terminé."""
    meta_path = PENDING_DIR / f"{pending_id}.json"
    try:
        supplier_guess, yaml_proposed, initial_prompt, auto_diagnosis, history = _run_refinement_loop(
            file_path=file_path,
            folder_name=folder_name,
            filename=filename,
        )
        logger.info(
            "boucle agentique terminée",
            fichier=filename,
            supplier=supplier_guess,
            confidence=auto_diagnosis.get("confidence", 0),
            iterations=len(history),
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.update({
            "status": "pending",
            "supplier_guess": supplier_guess,
            "yaml_proposed": yaml_proposed,
            "initial_prompt": initial_prompt,
            "auto_confidence": auto_diagnosis.get("confidence", 0),
            "auto_verdict": auto_diagnosis.get("verdict", ""),
            "auto_resume": auto_diagnosis.get("resume", ""),
            "auto_issues": auto_diagnosis.get("issues", []),
            "auto_suggestions": auto_diagnosis.get("suggestions", []),
            "auto_iterations": len(history),
            "auto_history": history,
        })
    except Exception as exc:
        logger.error("boucle agentique échouée", erreur=str(exc), fichier=filename)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = "pending"
        meta["yaml_proposed"] = f"# Génération échouée : {exc}\n# Complétez manuellement.\n"
        meta["auto_confidence"] = 0
        meta["auto_verdict"] = "à refaire"

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


@router.post("/ingest/unknown", response_model=UnknownIngestResponse, tags=["ingestion"])
def ingest_unknown(request: UnknownIngestRequest, background_tasks: BackgroundTasks) -> UnknownIngestResponse:
    """Reçoit un fichier de fournisseur inconnu, démarre la boucle agentique en arrière-plan et retourne immédiatement."""
    file_path = Path(request.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    existing = _find_pending_for_file(request.folder_name, request.filename)
    if existing is not None:
        file_path.unlink(missing_ok=True)
        logger.info("doublon évité", pending_id=existing["id"], filename=request.filename)
        return UnknownIngestResponse(
            pending_id=existing["id"],
            supplier_guess=existing.get("supplier_guess", ""),
            message="Une demande est déjà en attente de validation pour ce fichier.",
        )

    pending_id = request.pending_id or uuid.uuid4().hex

    # Enregistrement immédiat avec status "processing" — le watcher obtient sa réponse tout de suite
    meta = {
        "id": pending_id,
        "created_at": datetime.utcnow().isoformat(),
        "filename": request.filename,
        "folder_name": request.folder_name,
        "file_path": request.file_path,
        "supplier_guess": request.folder_name.lower().replace(" ", "_"),
        "yaml_proposed": "",
        "initial_prompt": "",
        "web_url": request.web_url,
        "status": "processing",
        "auto_confidence": None,
    }
    (PENDING_DIR / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Démarre la boucle agentique en arrière-plan (le watcher ne bloque plus)
    background_tasks.add_task(_refine_in_background, pending_id, file_path, request.folder_name, request.filename)

    logger.info("ingest démarré en arrière-plan", pending_id=pending_id, fichier=request.filename)

    return UnknownIngestResponse(
        pending_id=pending_id,
        supplier_guess=meta["supplier_guess"],
        message=f"Analyse IA en cours pour '{request.filename}' — le YAML sera disponible dans quelques minutes.",
    )
