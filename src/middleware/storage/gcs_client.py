from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import GoogleAuthError
from google.cloud import storage

from middleware.core.config import get_settings
from middleware.core.logging import get_logger

logger = get_logger(__name__)


def _upload_sync(file_path: Path, supplier_code: str, content_hash: str) -> str:
    """Charge le fichier dans GCS et retourne le chemin bucket/key.

    Les identifiants sont ceux du compte de service pointé par la variable
    d'environnement standard GOOGLE_APPLICATION_CREDENTIALS.
    """
    settings = get_settings()
    client = storage.Client()
    date_prefix = date.today().strftime("%Y-%m-%d")
    key = f"{supplier_code}/{date_prefix}/{content_hash}_{file_path.name}"
    client.bucket(settings.storage_bucket).blob(key).upload_from_filename(str(file_path))
    return f"{settings.storage_bucket}/{key}"


async def upload_raw_file(file_path: Path, supplier_code: str, content_hash: str) -> str | None:
    """Upload asynchrone vers GCS. Retourne le chemin bucket/key (stocké dans
    supplier_files.minio_path) ou None si échec."""
    try:
        gcs_path = await asyncio.to_thread(_upload_sync, file_path, supplier_code, content_hash)
        logger.info(
            "fichier brut archivé dans GCS",
            supplier_code=supplier_code,
            gcs_path=gcs_path,
        )
        return gcs_path
    except (GoogleAPIError, GoogleAuthError) as exc:
        logger.warning(
            "archivage GCS échoué — ingestion continue sans archivage",
            supplier_code=supplier_code,
            fichier=file_path.name,
            erreur=str(exc),
        )
        return None
