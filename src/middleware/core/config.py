from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration centrale chargée depuis les variables d'environnement."""

    model_config = SettingsConfigDict(
        env_prefix="MIDDLEWARE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Le .env est partagé avec le watcher (TENANT_ID, CLIENT_ID, etc.) :
        # on ignore les variables non préfixées MIDDLEWARE_ au lieu de planter.
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    debug: bool = False
    log_level: str = "INFO"
    environment: Literal["development", "staging", "production"] = "development"

    # ── Base de données ───────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://middleware:middleware@localhost:5432/middleware"
    )
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # ── Stockage objet ────────────────────────────────────────────────────────
    storage_endpoint: str = "http://localhost:9000"
    storage_bucket: str = "middleware-dev"
    storage_access_key: str = "minioadmin"
    storage_secret_key: str = "minioadmin"

    # ── Dossiers locaux ───────────────────────────────────────────────────────
    input_folder: str = "/data/input"
    output_folder: str = "/data/output"

    # ── SharePoint ────────────────────────────────────────────────────────────
    sharepoint_host: str = ""
    sharepoint_site_path: str = "/"

    # ── Codes fournisseurs SAGE (placeholder, source remplaçable) ───────────────
    # backend = "file" (placeholder CSV) aujourd'hui ; demain "http"/"db"/… cloud,
    # ajouté derrière la même interface resolve_sage_code() sans toucher au reste.
    sage_backend: str = "file"
    sage_mapping_file: str = "config/sage_codes.csv"


@lru_cache
def get_settings() -> Settings:
    """Retourne l'instance de configuration (singleton via cache)."""
    return Settings()
