from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from middleware.core.logging import get_logger
from middleware.storage.minio_client import upload_raw_file

logger = get_logger(__name__)
router = APIRouter()

PENDING_DIR = Path("/app/uploads/pending")


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


def _norm(text: str) -> str:
    """Normalise un texte Unicode (forme NFC) avant comparaison.

    SharePoint/Graph renvoie parfois les noms de fichiers en forme décomposée
    (ex: "é" = "e" + accent combinant séparé) plutôt que composée — visuellement
    identique mais PAS égal en comparaison stricte, ce qui faisait rater la
    détection de doublon pour tout fichier avec un caractère accentué.
    """
    return unicodedata.normalize("NFC", text or "")


def _find_pending_for_file(
    folder_name: str, filename: str, sharepoint_item_id: str | None = None
) -> dict | None:
    """Retourne une demande déjà ouverte (pending ou needs_support) pour ce fichier.

    Priorité à l'item ID SharePoint (clé stable), fallback sur (dossier, nom),
    comparés après normalisation Unicode (voir _norm). Les demandes déjà
    approved/rejected ne comptent pas — une nouvelle version du même fichier doit
    repartir sur une demande fraîche.
    """
    if not PENDING_DIR.exists():
        return None
    folder_name = _norm(folder_name)
    filename = _norm(filename)
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
            _norm(data.get("folder_name", "")) == folder_name
            and _norm(data.get("filename", "")) == filename
        ):
            return data
    return None


def _generate_ai_suggestion_background(
    pending_id: str, file_path: Path, folder_name: str, filename: str
) -> None:
    """Tâche de fond : génère la suggestion IA et met à jour le pending JSON.

    Tourne APRÈS que la réponse HTTP soit déjà repartie vers le watcher — l'appel
    Gemini (jusqu'à 1-2 min) ne bloque donc jamais le reste de l'API (interface,
    autres requêtes). Best-effort : si ça échoue, la demande reste avec un YAML
    vide (le bouton « Générer avec l'IA » de l'interface reste disponible).
    """
    try:
        from middleware.ai.yaml_generator import generate_yaml_from_excel

        supplier_guess, yaml_content, prompt, confidence = generate_yaml_from_excel(
            file_path=file_path, folder_name=folder_name, filename=filename,
        )
        yaml_proposed = _inject_sharepoint_folder(yaml_content, folder_name)
    except Exception as exc:
        logger.warning(
            "génération IA automatique échouée (tâche de fond) — demande reste sans suggestion",
            pending_id=pending_id,
            filename=filename,
            erreur=str(exc),
        )
        return

    meta_path = PENDING_DIR / f"{pending_id}.json"
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if meta.get("status") != "pending":
        # La demande a déjà été traitée/modifiée entre-temps — ne pas écraser.
        return

    meta["yaml_proposed"] = yaml_proposed
    meta["initial_prompt"] = prompt
    meta["confidence"] = confidence
    meta["confidence_source"] = "ai_generated"
    meta["supplier_guess"] = supplier_guess
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "suggestion IA (tâche de fond) prête", pending_id=pending_id, confidence=confidence
    )


@router.post("/ingest/unknown", response_model=UnknownIngestResponse, tags=["ingestion"])
async def ingest_unknown(
    request: UnknownIngestRequest, background_tasks: BackgroundTasks
) -> UnknownIngestResponse:
    """Reçoit un fichier inconnu, l'archive dans MinIO et crée une demande de
    validation — la suggestion IA (YAML + confiance) est générée en tâche de fond
    juste après, sans bloquer la réponse ni le reste de l'API.
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

    meta = {
        "id": pending_id,
        "created_at": datetime.utcnow().isoformat(),
        "filename": request.filename,
        "folder_name": request.folder_name,
        "file_path": request.file_path,
        "supplier_guess": supplier_guess,
        "yaml_proposed": "",
        "initial_prompt": "",
        "confidence": None,
        "confidence_source": None,
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

    # Suggestion IA en tâche de fond — ne bloque ni cette réponse ni le reste de l'API
    background_tasks.add_task(
        _generate_ai_suggestion_background,
        pending_id, file_path, request.folder_name, request.filename,
    )

    return UnknownIngestResponse(
        pending_id=pending_id,
        supplier_guess=supplier_guess,
        message=f"Fichier '{request.filename}' en attente de validation.",
    )
