from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.ai.yaml_generator import edit_yaml_with_ai, read_excel_preview
from middleware.core.exceptions import ParsingError
from middleware.core.logging import get_logger
from middleware.db.session import get_session
from middleware.delta.engine import compute_delta
from middleware.exporter.gery import NEW_ARTICLE_COLS, build_new_article_rows
from middleware.parser.yaml_loader import validate_mapping_yaml
from middleware.pipeline import parse_with_rule, process_and_export

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
