from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.ai.yaml_generator import diagnose_yaml_with_ai, edit_yaml_with_ai, read_excel_preview
from middleware.core.exceptions import ParsingError
from middleware.core.logging import get_logger
from middleware.db.session import get_session
from middleware.delta.engine import compute_delta
from middleware.exporter.gery import NEW_ARTICLE_COLS, build_new_article_rows
from middleware.parser.yaml_loader import validate_mapping_yaml
from middleware.pipeline import parse_with_rule, process_and_export
from middleware.sage_codes import resolve_sage_code

logger = get_logger(__name__)
router = APIRouter()

PENDING_DIR = Path("/app/uploads/pending")
CONFIG_DIR = Path("config/suppliers")
EXPORTS_DIR = Path("/app/exports")


def _load_pending(pending_id: str) -> dict:
    meta_path = PENDING_DIR / f"{pending_id}.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Demande introuvable : {pending_id}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _html_page(title: str, message: str, color: str = "#1a7f5a") -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>{title}</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f5f5f5}}
.card{{background:#fff;padding:2rem 3rem;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);text-align:center;max-width:500px}}
h1{{color:{color}}}p{{color:#555}}</style></head>
<body><div class="card"><h1>{title}</h1><p>{message}</p></div></body></html>"""
    return HTMLResponse(content=html)


@router.get("/review/pending", tags=["validation"])
def list_pending() -> list[dict]:
    """Liste les configurations YAML en attente de validation."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for meta_path in sorted(PENDING_DIR.glob("*.json")):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            result.append({
                "id": data["id"],
                "filename": data["filename"],
                "folder_name": data["folder_name"],
                "supplier_guess": data["supplier_guess"],
                "status": data["status"],
                "created_at": data["created_at"],
            })
        except Exception:
            continue
    return result


@router.get("/review/{pending_id}", tags=["validation"])
def get_pending(pending_id: str) -> dict:
    """Retourne le détail d'une demande de validation (YAML proposé inclus)."""
    return _load_pending(pending_id)


class UpdateYamlRequest(BaseModel):
    yaml_content: str


class UpdateYamlResponse(BaseModel):
    ok: bool
    supplier_code: str


@router.put("/review/{pending_id}", response_model=UpdateYamlResponse, tags=["validation"])
def update_pending_yaml(pending_id: str, request: UpdateYamlRequest) -> UpdateYamlResponse:
    """Valide et enregistre une édition du YAML proposé (avant approbation)."""
    meta = _load_pending(pending_id)

    if meta["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Cette demande a déjà été {meta['status']}.",
        )

    rule, erreurs = validate_mapping_yaml(request.yaml_content)
    if erreurs:
        raise HTTPException(status_code=422, detail=erreurs)
    assert rule is not None

    meta["yaml_proposed"] = request.yaml_content
    meta["supplier_guess"] = rule.supplier_code
    (PENDING_DIR / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("yaml édité sauvegardé", pending_id=pending_id, supplier_code=rule.supplier_code)

    return UpdateYamlResponse(ok=True, supplier_code=rule.supplier_code)


@router.get("/review/{pending_id}/preview", tags=["validation"])
def get_pending_preview(pending_id: str) -> dict:
    """Retourne un aperçu texte du fichier Excel source."""
    meta = _load_pending(pending_id)
    file_path = Path(meta.get("file_path", ""))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier source introuvable : {file_path}")
    return {"preview": read_excel_preview(file_path)}


@router.get("/review/{pending_id}/source-file", tags=["validation"])
def get_source_file(pending_id: str) -> FileResponse:
    """Télécharge le fichier Excel source d'une demande (pour l'ouvrir en 1 clic)."""
    meta = _load_pending(pending_id)
    file_path = Path(meta.get("file_path", ""))
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Fichier source introuvable.")
    return FileResponse(
        file_path,
        filename=meta.get("filename") or file_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class DiagnoseRequest(BaseModel):
    yaml_content: str


@router.post("/review/{pending_id}/diagnose", tags=["validation"])
def diagnose_yaml(pending_id: str, request: DiagnoseRequest) -> dict:
    """Diagnostic fonctionnel du YAML : parse le fichier et retourne un rapport qualité.

    Vérifie structurellement le YAML (Pydantic) puis l'applique sur le fichier source
    réel pour mesurer : produits trouvés, codes manquants, prix absents, erreurs de ligne.
    Renvoie un rapport lisible sans rien persister.
    """
    meta = _load_pending(pending_id)

    # 1. Validation structurelle
    rule, erreurs = validate_mapping_yaml(request.yaml_content)
    if erreurs or rule is None:
        return {
            "yaml_valid": False,
            "errors": erreurs or ["YAML invalide."],
            "parse_ok": False,
        }

    file_path = Path(meta.get("file_path", ""))
    if not file_path.exists():
        return {
            "yaml_valid": True,
            "errors": [],
            "parse_ok": False,
            "fatal": "Fichier source introuvable — impossible de tester le parsing.",
        }

    # 2. Parsing fonctionnel
    try:
        result = parse_with_rule(file_path, rule)
    except ParsingError as exc:
        return {
            "yaml_valid": True,
            "errors": [],
            "parse_ok": False,
            "fatal": str(exc),
        }

    products = result.products
    total = len(products)

    sans_code = sum(1 for p in products if not p.supplier_product_code.strip())
    sans_designation = sum(1 for p in products if not p.designation.strip())
    sans_prix = sum(1 for p in products if not p.prices and not p.variants)
    avec_prix = total - sans_prix

    warnings: list[str] = []
    if sans_code:
        warnings.append(f"{sans_code} produit(s) sans code article")
    if sans_designation:
        warnings.append(f"{sans_designation} produit(s) sans désignation")
    if sans_prix:
        pct = round(sans_prix / total * 100) if total else 0
        warnings.append(f"{sans_prix} produit(s) sans prix ({pct}% — souvent 'sur consultation')")
    if result.error_count:
        warnings.append(f"{result.error_count} ligne(s) en erreur lors du parsing")
    if total == 0:
        warnings.append("Aucun produit extrait — vérifiez les colonnes et la ligne de début de données")

    sample = [
        {
            "code": p.supplier_product_code,
            "designation": p.designation,
            "prix": float(p.prices[0].amount) if p.prices else None,
            "type_prix": p.prices[0].price_type if p.prices else None,
            "ligne": p.source_row,
        }
        for p in products[:5]
    ]

    feux = "vert" if not warnings and total > 0 else ("orange" if total > 0 else "rouge")

    return {
        "yaml_valid": True,
        "errors": [],
        "parse_ok": True,
        "feux": feux,
        "total_produits": total,
        "avec_prix": avec_prix,
        "sans_prix": sans_prix,
        "sans_code": sans_code,
        "sans_designation": sans_designation,
        "erreurs_parsing": result.error_count,
        "warnings": warnings,
        "sample": sample,
        "validity_start": result.file_metadata.validity_start.isoformat() if result.file_metadata.validity_start else None,
        "validity_end": result.file_metadata.validity_end.isoformat() if result.file_metadata.validity_end else None,
    }


class AiDiagnoseRequest(BaseModel):
    yaml_content: str


@router.post("/review/{pending_id}/ai-diagnose", tags=["validation"])
def ai_diagnose(pending_id: str, request: AiDiagnoseRequest) -> dict:
    """Diagnostic en deux passes : moteur Python puis juge IA (Gemini).

    1. Valide le YAML (Pydantic) et parse le fichier réel → rapport chiffré.
    2. Envoie le YAML + aperçu + résultats à Gemini → taux de confiance,
       verdict et points précis à corriger.
    """
    meta = _load_pending(pending_id)
    file_path = Path(meta.get("file_path", ""))

    # Passe 1 : diagnostic moteur Python (réutilise la logique de /diagnose)
    rule, erreurs = validate_mapping_yaml(request.yaml_content)
    if erreurs or rule is None:
        return {
            "python": {"yaml_valid": False, "errors": erreurs or ["YAML invalide."], "parse_ok": False},
            "ai": {"confidence": 0, "verdict": "à refaire", "issues": erreurs or [], "suggestions": []},
        }

    parse_results: dict = {"yaml_valid": True, "errors": [], "parse_ok": False}
    if file_path.exists():
        try:
            result = parse_with_rule(file_path, rule)
            products = result.products
            total = len(products)
            sans_prix = sum(1 for p in products if not p.prices and not p.variants)
            avec_prix = total - sans_prix
            sans_code = sum(1 for p in products if not p.supplier_product_code.strip())
            warnings: list[str] = []
            if sans_code:
                warnings.append(f"{sans_code} produit(s) sans code article")
            if sans_prix:
                pct = round(sans_prix / total * 100) if total else 0
                warnings.append(f"{sans_prix} produit(s) sans prix ({pct}%)")
            if result.error_count:
                warnings.append(f"{result.error_count} ligne(s) en erreur")
            if total == 0:
                warnings.append("Aucun produit extrait")
            parse_results = {
                "yaml_valid": True, "errors": [], "parse_ok": True,
                "total_produits": total, "avec_prix": avec_prix,
                "sans_prix": sans_prix, "sans_code": sans_code,
                "erreurs_parsing": result.error_count, "warnings": warnings,
                "feux": "vert" if not warnings and total > 0 else ("orange" if total > 0 else "rouge"),
                "sample": [
                    {
                        "code": p.supplier_product_code,
                        "designation": p.designation,
                        "prix": float(p.prices[0].amount) if p.prices else (
                            float(p.variants[0].prices[0].amount) if p.variants and p.variants[0].prices else None
                        ),
                        "ligne": p.source_row,
                    }
                    for p in products[:5]
                ],
            }
        except ParsingError as exc:
            parse_results = {"yaml_valid": True, "errors": [], "parse_ok": False, "fatal": str(exc)}

    # Passe 2 : juge IA — reçoit le fichier réel + aperçu Gery + résultats Python
    gery_rows: list = []
    if parse_results.get("parse_ok") and rule is not None:
        try:
            from middleware.delta.engine import compute_delta
            from middleware.exporter.gery import build_new_article_rows
            from middleware.sage_codes import resolve_sage_code
            _result = parse_with_rule(file_path, rule)
            _delta = compute_delta(_result.products, known_hashes={})
            gery_rows = build_new_article_rows(
                _delta,
                rule.gery_export,
                _result.file_metadata.validity_start,
                _result.file_metadata.validity_end,
                resolve_sage_code(rule.supplier_code),
            )
        except Exception:
            gery_rows = []

    ai_result = diagnose_yaml_with_ai(
        request.yaml_content,
        file_path,
        parse_results,
        gery_rows=gery_rows,
    )

    return {"python": parse_results, "ai": ai_result}


class ExportPreviewRequest(BaseModel):
    yaml_content: str


@router.post("/review/{pending_id}/export-preview", tags=["validation"])
def export_preview(pending_id: str, request: ExportPreviewRequest) -> dict:
    """Aperçu (dry-run) des lignes NEW_ARTICLE qui seraient générées pour Gery.

    Parse le fichier source avec le YAML fourni et construit les lignes en mémoire,
    sans rien persister ni écrire. Permet de relire le résultat avant de valider.
    """
    meta = _load_pending(pending_id)

    rule, erreurs = validate_mapping_yaml(request.yaml_content)
    if erreurs or rule is None:
        raise HTTPException(status_code=422, detail=erreurs or ["YAML invalide."])

    file_path = Path(meta.get("file_path", ""))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Fichier source introuvable.")

    try:
        result = parse_with_rule(file_path, rule)
    except ParsingError as exc:
        raise HTTPException(status_code=422, detail=[str(exc)]) from exc

    # Delta "première ingestion" (tout en CREATE) → aperçu de ce qui sortirait
    delta = compute_delta(result.products, known_hashes={})
    rows = build_new_article_rows(
        delta,
        rule.gery_export,
        result.file_metadata.validity_start,
        result.file_metadata.validity_end,
        resolve_sage_code(rule.supplier_code),
    )
    return {
        "columns": NEW_ARTICLE_COLS,
        "rows": rows,
        "line_count": len(rows),
        "products_parsed": len(result.products),
        "export_enabled": rule.gery_export.enabled,
    }


class AiEditRequest(BaseModel):
    yaml_content: str
    instruction: str


@router.post("/review/{pending_id}/ai-edit", tags=["validation"])
def ai_edit(pending_id: str, request: AiEditRequest) -> dict:
    """Modifie le YAML via une instruction en langage naturel (assistant IA).

    Renvoie le YAML mis à jour + son statut de validation (sans rien enregistrer ;
    c'est l'interface qui décide d'appliquer/sauver).
    """
    meta = _load_pending(pending_id)
    file_path = Path(meta.get("file_path", ""))
    preview = read_excel_preview(file_path) if file_path.exists() else ""

    try:
        new_yaml = edit_yaml_with_ai(request.yaml_content, request.instruction, preview)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erreur de l'assistant IA : {exc}") from exc

    rule, erreurs = validate_mapping_yaml(new_yaml)
    return {
        "yaml": new_yaml,
        "valid": bool(rule is not None and not erreurs),
        "errors": erreurs or [],
    }


@router.get("/review/{pending_id}/approve", response_class=HTMLResponse, tags=["validation"])
async def approve_pending(
    pending_id: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Approuve le YAML, le sauvegarde, puis traite le fichier (DB + MinIO + export CSV)."""
    meta = _load_pending(pending_id)

    if meta["status"] != "pending":
        return _html_page(
            "Déjà traité",
            f"Cette demande a déjà été {meta['status']}.",
            color="#888",
        )

    # Valider le YAML avant toute écriture
    yaml_content = meta["yaml_proposed"]
    rule, erreurs = validate_mapping_yaml(yaml_content)
    if erreurs or rule is None:
        return _html_page(
            "YAML invalide",
            "Corrigez la configuration avant de valider :<br>" + "<br>".join(erreurs),
            color="#c0392b",
        )
    supplier_code = rule.supplier_code

    # Sauvegarder le YAML dans le référentiel de configuration
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    yaml_path = CONFIG_DIR / f"{supplier_code}_v1.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    logger.info("yaml approuvé sauvegardé", path=str(yaml_path))

    file_path = Path(meta.get("file_path", ""))
    if not file_path.exists():
        return _html_page(
            "Fichier source introuvable",
            f"Le YAML de <strong>{supplier_code}</strong> est enregistré, mais le fichier "
            f"source est introuvable — à retraiter manuellement.",
            color="#c0392b",
        )

    # Traitement complet via le service partagé (archivage MinIO, DB, export CSV)
    try:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        _, _, export_result = await process_and_export(
            session,
            rule,
            file_path,
            EXPORTS_DIR,
            original_filename=meta.get("filename"),
            sharepoint_item_id=meta.get("sharepoint_item_id"),
        )
    except Exception as exc:
        await session.rollback()
        logger.error("erreur traitement après approbation", erreur=str(exc))
        return _html_page(
            "Erreur de traitement",
            f"Le YAML est enregistré mais le traitement a échoué : {exc}",
            color="#c0392b",
        )

    exports = [f.path.name for f in export_result.files]

    # Marquer approuvé + mémoriser les fichiers générés (pour le téléchargement)
    meta["status"] = "approved"
    meta["supplier_code"] = supplier_code
    meta["exports"] = exports
    (PENDING_DIR / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    exports_html = "<br>".join(
        f"• {e} ({f.line_count} lignes)" for e, f in zip(exports, export_result.files, strict=True)
    ) or "Aucun fichier généré (export désactivé ou aucun changement)."
    return _html_page(
        "✓ YAML approuvé",
        f"Le fournisseur <strong>{supplier_code}</strong> est configuré et traité.<br><br>"
        f"Fichiers Gery générés :<br>{exports_html}",
        color="#1a7f5a",
    )


@router.get("/review/{pending_id}/reject", response_class=HTMLResponse, tags=["validation"])
def reject_pending(pending_id: str) -> HTMLResponse:
    """Rejette le YAML proposé."""
    meta = _load_pending(pending_id)

    if meta["status"] != "pending":
        return _html_page("Déjà traité", f"Cette demande a déjà été {meta['status']}.", color="#888")

    meta["status"] = "rejected"
    (PENDING_DIR / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("yaml rejeté", pending_id=pending_id, supplier=meta["supplier_guess"])

    return _html_page(
        "✗ YAML rejeté",
        "La configuration a été rejetée. Contactez l'équipe technique pour configurer ce fournisseur manuellement.",
        color="#c0392b",
    )


@router.get("/review/{pending_id}/download", tags=["validation"])
def download_export(pending_id: str, filename: str | None = None) -> FileResponse:
    """Télécharge un fichier Gery (CSV) généré pour une demande approuvée.

    `filename` doit faire partie des exports enregistrés pour cette demande
    (protection contre la traversée de chemin). Par défaut, le premier export.
    """
    meta = _load_pending(pending_id)
    exports = meta.get("exports") or []
    if not exports:
        raise HTTPException(status_code=404, detail="Aucun fichier généré pour cette demande.")

    target = filename or exports[0]
    if target not in exports:
        raise HTTPException(status_code=404, detail=f"Fichier inconnu : {target}")

    path = EXPORTS_DIR / target
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {target}")

    return FileResponse(path, media_type="text/csv", filename=target)
