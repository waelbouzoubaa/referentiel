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
            data.get("status") == "pending"
            and data.get("folder_name") == folder_name
            and data.get("filename") == filename
        ):
            return data
    return None


@router.post("/ingest/unknown", response_model=UnknownIngestResponse, tags=["ingestion"])
def ingest_unknown(request: UnknownIngestRequest) -> UnknownIngestResponse:
    """Reçoit un fichier de fournisseur inconnu, génère un YAML via IA et notifie pour validation."""
    file_path = Path(request.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    # Dédup : une demande est déjà en attente pour ce fichier → pas de doublon
    # (le watcher peut ré-émettre le même fichier à chaque scan tant qu'il est inconnu).
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    existing = _find_pending_for_file(request.folder_name, request.filename)
    if existing is not None:
        file_path.unlink(missing_ok=True)  # supprime la copie téléchargée (doublon)
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

    initial_prompt = ""
    try:
        supplier_guess, yaml_proposed, initial_prompt = generate_yaml_from_excel(
            file_path=file_path,
            folder_name=request.folder_name,
            filename=request.filename,
        )
    except Exception as exc:
        logger.error("génération YAML IA échouée", erreur=str(exc))
        supplier_guess = request.folder_name.lower().replace(" ", "_")
        yaml_proposed = f"# Génération automatique échouée : {exc}\n# Complétez ce YAML manuellement.\nsupplier_code: \"{supplier_guess}\"\n"
        initial_prompt = f"(génération échouée : {exc})"

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
        "initial_prompt": initial_prompt,
        "web_url": request.web_url,
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
