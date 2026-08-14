from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from middleware.core.logging import get_logger
from middleware.storage.minio_client import upload_raw_file

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
    web_url: str | None = None
    sharepoint_item_id: str | None = None


class UnknownIngestResponse(BaseModel):
    pending_id: str
    supplier_guess: str
    message: str


def _inject_sharepoint_folder(yaml_content: str, folder_name: str) -> str:
    """Corrige sharepoint_folder dans le YAML généré avec le vrai dossier SharePoint source."""
    import re
    folder_line = f'sharepoint_folder: "{folder_name}"'

    if re.search(r'^sharepoint_folder:', yaml_content, re.MULTILINE):
        yaml_content = re.sub(r'^sharepoint_folder:.*$', folder_line, yaml_content, flags=re.MULTILINE)
    else:
        yaml_content = re.sub(
            r'(^supplier_code:.*$)',
            r'\1\n' + folder_line,
            yaml_content,
            count=1,
            flags=re.MULTILINE,
        )

    if not re.search(r'^filename_keywords:', yaml_content, re.MULTILINE):
        yaml_content = re.sub(
            r'(^sharepoint_folder:.*$)',
            r'\1\nfilename_keywords: []',
            yaml_content,
            count=1,
            flags=re.MULTILINE,
        )

    return yaml_content


def _find_pending_for_file(
    folder_name: str, filename: str, sharepoint_item_id: str | None = None
) -> dict | None:
    """Retourne une demande déjà ouverte (pending ou needs_support) pour ce fichier.

    Priorité à l'item ID SharePoint (clé stable), fallback sur (dossier, nom). Les
    demandes déjà approved/rejected ne comptent pas — une nouvelle version du même
    fichier doit repartir sur une demande fraîche.
    """
    if not PENDING_DIR.exists():
        return None
    for meta_path in PENDING_DIR.glob("*.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("status") not in ("pending", "needs_support"):
            continue
        if sharepoint_item_id and data.get("sharepoint_item_id") == sharepoint_item_id:
            return data
        if (
            data.get("folder_name") == folder_name
            and data.get("filename") == filename
        ):
            return data
    return None


@router.post("/ingest/unknown", response_model=UnknownIngestResponse, tags=["ingestion"])
async def ingest_unknown(request: UnknownIngestRequest) -> UnknownIngestResponse:
    """Reçoit un fichier inconnu, l'archive dans MinIO, génère une suggestion IA
    (YAML + confiance) et crée une demande de validation.

    La génération IA est best-effort : si Gemini échoue, la demande est quand même
    créée (YAML vide, comme avant) — l'utilisateur peut relancer avec le bouton
    « Générer avec l'IA » dans l'interface. Rien ne se perd.
    """
    file_path = Path(request.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    existing = _find_pending_for_file(request.folder_name, request.filename, request.sharepoint_item_id)
    if existing is not None:
        file_path.unlink(missing_ok=True)
        logger.info(
            "doublon évité — demande déjà en attente",
            pending_id=existing["id"],
            filename=request.filename,
        )
        return UnknownIngestResponse(
            pending_id=existing["id"],
            supplier_guess=existing.get("supplier_guess", ""),
            message="Une demande est déjà en attente de validation pour ce fichier.",
        )

    pending_id = request.pending_id or uuid.uuid4().hex
    supplier_guess = request.folder_name.lower().replace(" ", "_").replace("-", "_")

    # Archive le fichier brut dans MinIO dès la détection
    content_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()[:12]
    minio_path = await upload_raw_file(file_path, supplier_guess, content_hash)

    # Suggestion IA immédiate (best-effort — la demande est créée même si ça échoue)
    yaml_proposed = ""
    initial_prompt = ""
    confidence: int | None = None
    confidence_source: str | None = None
    try:
        from middleware.ai.yaml_generator import generate_yaml_from_excel

        ai_supplier_guess, yaml_content, prompt, confidence = generate_yaml_from_excel(
            file_path=file_path,
            folder_name=request.folder_name,
            filename=request.filename,
        )
        yaml_proposed = _inject_sharepoint_folder(yaml_content, request.folder_name)
        initial_prompt = prompt
        confidence_source = "ai_generated"
        supplier_guess = ai_supplier_guess
    except Exception as exc:
        logger.warning(
            "génération IA automatique échouée à l'ingestion — demande créée sans suggestion",
            filename=request.filename,
            erreur=str(exc),
        )

    meta = {
        "id": pending_id,
        "created_at": datetime.utcnow().isoformat(),
        "filename": request.filename,
        "folder_name": request.folder_name,
        "file_path": request.file_path,
        "supplier_guess": supplier_guess,
        "yaml_proposed": yaml_proposed,
        "initial_prompt": initial_prompt,
        "confidence": confidence,
        "confidence_source": confidence_source,
        "web_url": request.web_url,
        "sharepoint_item_id": request.sharepoint_item_id,
        "minio_path": minio_path,
        "status": "pending",
    }
    (PENDING_DIR / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "nouveau fichier en attente de validation",
        pending_id=pending_id,
        filename=request.filename,
        folder=request.folder_name,
        minio_path=minio_path,
    )

    # Notifier n8n
    try:
        httpx.post(
            N8N_WEBHOOK_URL,
            json={
                "pending_id": pending_id,
                "filename": request.filename,
                "folder_name": request.folder_name,
                "supplier_guess": supplier_guess,
                "approve_url": f"{API_BASE_URL}/api/v1/review/{pending_id}/approve",
                "reject_url": f"{API_BASE_URL}/api/v1/review/{pending_id}/reject",
            },
            timeout=10,
        )
        logger.info("n8n notifié", pending_id=pending_id)
    except Exception as exc:
        logger.warning("notification n8n échouée", erreur=str(exc))

    return UnknownIngestResponse(
        pending_id=pending_id,
        supplier_guess=supplier_guess,
        message=f"Fichier '{request.filename}' en attente de validation.",
    )
