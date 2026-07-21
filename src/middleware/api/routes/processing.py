from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from middleware.api.schemas import (
    DeltaSummary,
    GenerateExportsRequest,
    GenerateExportsResponse,
    ProcessFileRequest,
    ProcessFileResponse,
)
from middleware.core.exceptions import ParsingError
from middleware.core.logging import get_logger
from middleware.delta.engine import compute_delta
from middleware.parser.grammar import MappingRule
from middleware.parser.pivot import ParsingResult
from middleware.parser.yaml_loader import load_all_mappings
from middleware.pipeline import parse_with_rule

PENDING_DIR = Path("/app/uploads/pending")

logger = get_logger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# POST /process-file
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/process-file", response_model=ProcessFileResponse, tags=["traitement"])
async def process_file(request: ProcessFileRequest) -> ProcessFileResponse:
    """Parse un fichier fournisseur et calcule le delta par rapport à l'état connu.

    En mode dry_run=True, aucune donnée n'est persistée en base.
    La règle YAML est chargée depuis config/suppliers/.

    Args:
        request: supplier_code, file_path (local), dry_run flag.

    Returns:
        Résumé du parsing et du delta.
    """
    path = Path(request.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    rule = _load_rule(request.supplier_code)

    result = _parse_with_rule(path, rule)

    # Delta : aucun état connu en mode dry_run (simule une première ingestion)
    # En production, known_hashes viendrait de la base de données
    delta = compute_delta(result.products, known_hashes={})
    delta_summary = DeltaSummary(
        creates=len(delta.creates),
        updates=len(delta.updates),
        price_changes=len(delta.price_changes),
        deletes=len(delta.deletes),
        reactivates=len(delta.reactivates),
        unchanged=delta.unchanged,
        total_changes=delta.total_changes,
    )

    logger.info(
        "process-file terminé",
        supplier_code=request.supplier_code,
        fichier=path.name,
        produits=len(result.products),
        delta=delta_summary.model_dump(),
    )

    return ProcessFileResponse(
        supplier_code=result.supplier_code,
        filename=result.filename,
        products_parsed=len(result.products),
        error_count=result.error_count,
        delta=delta_summary,
        dry_run=request.dry_run,
        parsed_at=result.parsed_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /generate-gery-exports
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/generate-gery-exports", response_model=GenerateExportsResponse, tags=["exports"])
async def generate_gery_exports_endpoint(
    request: GenerateExportsRequest,
) -> GenerateExportsResponse:
    """Reçoit un fichier d'un fournisseur connu et crée une demande de validation.

    Tout fichier — connu ou non — passe désormais par une validation métier avant
    export : le YAML connu est proposé comme suggestion (confiance haute), mais
    l'export réel (DB + CSV Gery) n'est déclenché qu'à l'approbation dans l'UI
    (voir /review/{id}/approve). Un contrôle de cohérence signale en plus les
    fichiers qui ne correspondent visiblement plus au YAML connu (0 produit, 0
    prix, trop d'erreurs) — confiance réduite, alerte affichée dans l'UI.
    """
    path = Path(request.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    rule = _load_rule(request.supplier_code)

    parse_result = _parse_with_rule(path, rule)
    issues = _check_coherence(parse_result, rule)

    pending_id = _create_pending_review(request, path, rule, issues)
    logger.info(
        "fichier d'un fournisseur connu routé vers validation métier",
        supplier_code=request.supplier_code,
        fichier=path.name,
        issues=issues,
        pending_id=pending_id,
    )
    return GenerateExportsResponse(
        supplier_code=request.supplier_code,
        files=[],
        generated_at=datetime.utcnow(),
        pending_id=pending_id,
        pending_issues=issues,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _check_coherence(result: ParsingResult, rule: MappingRule) -> list[str]:
    """Vérifie que le parsing est cohérent. Retourne les problèmes détectés (vide = OK)."""
    issues: list[str] = []
    total = len(result.products)

    if total == 0:
        issues.append("Aucun produit extrait — structure du fichier probablement incompatible avec le YAML actuel")
        return issues

    if result.error_count > 0 and result.error_count / total > 0.5:
        pct = round(result.error_count / total * 100)
        issues.append(
            f"{result.error_count}/{total} lignes en erreur ({pct}%) — "
            "les colonnes ont peut-être bougé dans ce fichier"
        )

    if rule.prices:
        avec_prix = sum(1 for p in result.products if p.prices or p.variants)
        if avec_prix == 0:
            issues.append(
                "0 produit avec prix alors que le YAML en attend — "
                "la colonne prix est probablement incorrecte ou décalée"
            )

    return issues


def _create_pending_review(
    request: GenerateExportsRequest,
    file_path: Path,
    rule: MappingRule,
    issues: list[str],
) -> str:
    """Crée une demande de validation pour un fichier d'un fournisseur connu.

    Le YAML déjà committé est proposé comme suggestion (confiance haute, réduite
    si des soucis de cohérence sont détectés) — reste soumis à validation métier,
    jamais appliqué automatiquement.
    """
    # Recharge le YAML brut pour l'afficher dans l'UI
    config_dir = Path("config/suppliers")
    yaml_file = config_dir / f"{rule.supplier_code}_v1.yaml"
    yaml_content = yaml_file.read_text(encoding="utf-8") if yaml_file.exists() else ""

    pending_id = uuid.uuid4().hex
    PENDING_DIR.mkdir(parents=True, exist_ok=True)

    # Copie le fichier dans le dossier pending pour qu'il survive à la suppression
    # du fichier temporaire par le watcher (finally: tmp_path.unlink()).
    import shutil
    saved_path = PENDING_DIR / f"{pending_id}{file_path.suffix}"
    shutil.copy2(file_path, saved_path)

    meta = {
        "id": pending_id,
        "created_at": datetime.utcnow().isoformat(),
        "filename": request.original_filename or file_path.name,
        "folder_name": request.folder_name or rule.supplier_code,
        "file_path": str(saved_path),
        "supplier_guess": rule.supplier_code,
        "yaml_proposed": yaml_content,
        "initial_prompt": "",
        "web_url": request.web_url,
        "sharepoint_item_id": request.sharepoint_item_id,
        "status": "pending",
        "confidence": 60 if issues else 90,
        "confidence_source": "known_yaml",
        "anomaly": bool(issues),
        "anomaly_issues": issues,
    }
    (PENDING_DIR / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return pending_id


def _load_rule(supplier_code: str) -> MappingRule:
    """Charge la règle YAML active pour un fournisseur."""
    config_dir = Path("config/suppliers")
    mappings = load_all_mappings(config_dir)
    rule = mappings.get(supplier_code)
    if rule is None:
        raise HTTPException(
            status_code=404,
            detail=f"Fournisseur inconnu ou aucune règle YAML : '{supplier_code}'. "
                   f"Fournisseurs disponibles : {list(mappings.keys())}",
        )
    return rule


def _parse_with_rule(path: Path, rule: MappingRule):
    """Dispatch vers le bon parseur (cf. pipeline.parse_with_rule), erreurs en 422."""
    try:
        return parse_with_rule(path, rule)
    except (ValueError, ParsingError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
