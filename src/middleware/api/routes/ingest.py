from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from middleware.ai.yaml_generator import generate_yaml_from_excel
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


class UnknownIngestResponse(BaseModel):
    pending_id: str
    supplier_guess: str
    message: str


@router.post("/ingest/unknown", response_model=UnknownIngestResponse, tags=["ingestion"])
def ingest_unknown(request: UnknownIngestRequest) -> UnknownIngestResponse:
    """Reçoit un fichier de fournisseur inconnu, génère un YAML via IA et notifie pour validation."""
    file_path = Path(request.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    pending_id = request.pending_id or uuid.uuid4().hex

    try:
        supplier_guess, yaml_proposed = generate_yaml_from_excel(
            file_path=file_path,
            folder_name=request.folder_name,
            filename=request.filename,
        )
    except Exception as exc:
        logger.error("génération YAML IA échouée", erreur=str(exc))
        supplier_guess = request.folder_name.lower().replace(" ", "_")
        yaml_proposed = f"# Génération automatique échouée : {exc}\n# Complétez ce YAML manuellement.\nsupplier_code: \"{supplier_guess}\"\n"

    # Stocker les métadonnées
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": pending_id,
        "created_at": datetime.utcnow().isoformat(),
        "filename": request.filename,
        "folder_name": request.folder_name,
        "file_path": request.file_path,
        "supplier_guess": supplier_guess,
        "yaml_proposed": yaml_proposed,
        "status": "pending",
    }
    (PENDING_DIR / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Notifier n8n
    approve_url = f"{API_BASE_URL}/api/v1/review/{pending_id}/approve"
    reject_url = f"{API_BASE_URL}/api/v1/review/{pending_id}/reject"

    try:
        httpx.post(
            N8N_WEBHOOK_URL,
            json={
                "pending_id": pending_id,
                "filename": request.filename,
                "folder_name": request.folder_name,
                "supplier_guess": supplier_guess,
                "yaml_proposed": yaml_proposed,
                "approve_url": approve_url,
                "reject_url": reject_url,
            },
            timeout=10,
        )
        logger.info("n8n notifié", pending_id=pending_id)
    except Exception as exc:
        logger.warning("notification n8n échouée", erreur=str(exc))

    return UnknownIngestResponse(
        pending_id=pending_id,
        supplier_guess=supplier_guess,
        message=f"YAML généré pour '{supplier_guess}', en attente de validation humaine.",
    )
