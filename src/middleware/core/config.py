from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
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

    # ── Stockage objet (GCS) ──────────────────────────────────────────────────
    # Authentification : variable standard GOOGLE_APPLICATION_CREDENTIALS
    # (chemin du JSON du compte de service), lue directement par la lib Google.
    storage_bucket: str = "ramery-ai-features-integration-catalogue-article"

    # ── Dossiers locaux ───────────────────────────────────────────────────────
    input_folder: str = "/data/input"
    output_folder: str = "/data/output"

    # ── SharePoint ────────────────────────────────────────────────────────────
    sharepoint_host: str = ""
    sharepoint_site_path: str = "/"

    # ── Dépôt des exports Gery dans SharePoint (dossier Out) ──────────────────
    # Désactivé par défaut. Les identifiants Azure AD sont les mêmes variables non
    # préfixées que celles du watcher (TENANT_ID, CLIENT_ID, CLIENT_SECRET,
    # CERT_THUMBPRINT, CERT_PRIVATE_KEY_PATH, SHAREPOINT_HOST) : un seul .env.
    sharepoint_out_enabled: bool = False
    sharepoint_out_host: str = Field(
        "", validation_alias=AliasChoices("MIDDLEWARE_SHAREPOINT_OUT_HOST", "SHAREPOINT_HOST")
    )
    sharepoint_out_site_path: str = ""
    sharepoint_out_folder: str = "Out"
    azure_tenant_id: str = Field(
        "", validation_alias=AliasChoices("MIDDLEWARE_AZURE_TENANT_ID", "TENANT_ID")
    )
    azure_client_id: str = Field(
        "", validation_alias=AliasChoices("MIDDLEWARE_AZURE_CLIENT_ID", "CLIENT_ID")
    )
    azure_client_secret: str = Field(
        "", validation_alias=AliasChoices("MIDDLEWARE_AZURE_CLIENT_SECRET", "CLIENT_SECRET")
    )
    azure_cert_thumbprint: str = Field(
        "", validation_alias=AliasChoices("MIDDLEWARE_AZURE_CERT_THUMBPRINT", "CERT_THUMBPRINT")
    )
    azure_cert_private_key_path: str = Field(
        "",
        validation_alias=AliasChoices(
            "MIDDLEWARE_AZURE_CERT_PRIVATE_KEY_PATH", "CERT_PRIVATE_KEY_PATH"
        ),
    )

    # ── Codes fournisseurs SAGE (placeholder, source remplaçable) ───────────────
    # backend = "file" (placeholder CSV) aujourd'hui ; demain "http"/"db"/… cloud,
    # ajouté derrière la même interface resolve_sage_code() sans toucher au reste.
    sage_backend: str = "file"
    sage_mapping_file: str = "config/sage_codes.csv"

    # ── Notification email (demande d'aide support) ──────────────────────────
    # URL publique de l'interface de validation (Streamlit), incluse dans l'email
    # pour que le support clique directement dessus. Vide = pas de lien dans l'email.
    review_ui_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    support_notify_email: str = ""


@lru_cache
def get_settings() -> Settings:
    """Retourne l'instance de configuration (singleton via cache)."""
    return Settings()
