from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from middleware.core.config import get_settings
from middleware.core.logging import get_logger

logger = get_logger(__name__)


def _get_s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _upload_sync(file_path: Path, supplier_code: str, content_hash: str) -> str:
    """Charge le fichier dans MinIO et retourne le chemin bucket/key."""
    settings = get_settings()
    s3 = _get_s3_client()
    date_prefix = date.today().strftime("%Y-%m-%d")
    key = f"{supplier_code}/{date_prefix}/{content_hash}_{file_path.name}"
    s3.upload_file(str(file_path), settings.storage_bucket, key)
    return f"{settings.storage_bucket}/{key}"


async def upload_raw_file(file_path: Path, supplier_code: str, content_hash: str) -> str | None:
    """Upload asynchrone vers MinIO. Retourne le minio_path ou None si échec."""
    try:
        minio_path = await asyncio.to_thread(_upload_sync, file_path, supplier_code, content_hash)
        logger.info(
            "fichier brut archivé dans MinIO",
            supplier_code=supplier_code,
            minio_path=minio_path,
        )
        return minio_path
    except (BotoCoreError, ClientError) as exc:
        logger.warning(
            "archivage MinIO échoué — ingestion continue sans archivage",
            supplier_code=supplier_code,
            fichier=file_path.name,
            erreur=str(exc),
        )
        return None
