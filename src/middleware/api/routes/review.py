from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from middleware.ai.yaml_generator import read_excel_preview
from middleware.core.logging import get_logger
from middleware.delta.engine import compute_delta
from middleware.exporter.gery import generate_gery_exports
from middleware.parser.grammar import MappingRule
from middleware.parser.matrix_extractor import parse_matrix_file
from middleware.parser.multi_table_extractor import parse_multi_table_file
from middleware.parser.table_extractor import parse_table_file
from middleware.parser.yaml_loader import load_mapping_rule, validate_mapping_yaml

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


@router.get("/review/{pending_id}/approve", response_class=HTMLResponse, tags=["validation"])
def approve_pending(pending_id: str) -> HTMLResponse:
    """Approuve le YAML proposé, le sauvegarde et retraite le fichier."""
    meta = _load_pending(pending_id)

    if meta["status"] != "pending":
        return _html_page(
            "Déjà traité",
            f"Cette demande a déjà été {meta['status']}.",
            color="#888",
        )

    yaml_content = meta["yaml_proposed"]
    supplier_code = meta["supplier_guess"]

    # Sauvegarder le YAML
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    yaml_path = CONFIG_DIR / f"{supplier_code}_v1.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    logger.info("yaml approuvé sauvegardé", path=str(yaml_path))

    # Retraiter le fichier en attente
    file_path = Path(meta.get("file_path", ""))
    exports_info = []
    if file_path.exists():
        try:
            rule = load_mapping_rule(yaml_path)
            result = _parse_with_rule(file_path, rule)
            delta = compute_delta(result.products, known_hashes={})
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            export_result = generate_gery_exports(
                delta=delta,
                export_config=rule.gery_export,
                supplier_code=supplier_code,
                output_dir=EXPORTS_DIR,
                validity_start=result.file_metadata.validity_start,
                validity_end=result.file_metadata.validity_end,
            )
            exports_info = [f"{f.kind} ({f.line_count} lignes)" for f in export_result.files]
            logger.info("fichier retraité après approbation", supplier=supplier_code, exports=exports_info)
        except Exception as exc:
            logger.error("erreur retraitement après approbation", erreur=str(exc))
            exports_info = [f"Erreur : {exc}"]
    else:
        exports_info = ["Fichier source introuvable — à retraiter manuellement."]

    # Mettre à jour le statut
    meta["status"] = "approved"
    (PENDING_DIR / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    exports_html = "<br>".join(f"• {e}" for e in exports_info) or "Aucun export généré."
    return _html_page(
        "✓ YAML approuvé",
        f"Le fournisseur <strong>{supplier_code}</strong> est maintenant configuré.<br><br>"
        f"Exports Gery générés :<br>{exports_html}",
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


def _parse_with_rule(path: Path, rule: MappingRule):
    if rule.extraction_mode == "table":
        return parse_table_file(path, rule)
    elif rule.extraction_mode == "matrix":
        return parse_matrix_file(path, rule)
    elif rule.extraction_mode == "multi_table":
        return parse_multi_table_file(path, rule)
    else:
        raise ValueError(f"Mode non supporté : {rule.extraction_mode}")
