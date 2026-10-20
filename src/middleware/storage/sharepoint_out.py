"""Dépôt des fichiers d'export Gery dans le dossier Out d'un site SharePoint (Microsoft Graph).

Sens inverse du watcher : ici l'application ÉCRIT. Un fichier déjà présent sous le même nom
n'est jamais écrasé (conflictBehavior=fail) : le dépôt est alors signalé en échec.

Authentification : certificat (CERT_THUMBPRINT + CERT_PRIVATE_KEY_PATH) si renseigné,
sinon secret client — mêmes variables que le watcher.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
import msal

from middleware.core.config import Settings, get_settings
from middleware.core.logging import get_logger

logger = get_logger(__name__)

GRAPH_URL = "https://graph.microsoft.com/v1.0"
# Au-delà de cette taille on passe par une session d'upload (par blocs) plutôt qu'un PUT simple.
SIMPLE_UPLOAD_MAX_BYTES = 4 * 1000 * 1000
# Graph exige des blocs multiples de 320 Kio.
CHUNK_BYTES = 10 * 320 * 1024
HTTP_TIMEOUT = 60.0


class SharePointOutError(Exception):
    """Échec du dépôt (configuration, authentification ou réponse Graph)."""


class FileAlreadyExistsError(SharePointOutError):
    """Un fichier du même nom existe déjà dans le dossier de destination."""


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    web_url: str | None = None
    error: str | None = None


def _graph_error(resp: httpx.Response) -> SharePointOutError:
    try:
        err = resp.json().get("error", {})
        detail = f"{err.get('code', '')} : {err.get('message', '')}".strip(" :")
    except ValueError:
        detail = resp.text[:200]
    return SharePointOutError(f"Graph HTTP {resp.status_code} — {detail}")


class SharePointOutClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._s = settings
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT)
        self._token_provider = token_provider or self._msal_token
        self._msal_app: msal.ConfidentialClientApplication | None = None
        self._drive_id: str | None = None

    # ── Authentification ────────────────────────────────────────────────────
    def _credential(self) -> str | dict[str, str]:
        s = self._s
        if s.azure_cert_thumbprint and s.azure_cert_private_key_path:
            return {
                "thumbprint": s.azure_cert_thumbprint.replace(":", ""),
                "private_key": Path(s.azure_cert_private_key_path).read_text(),
            }
        if s.azure_client_secret:
            return s.azure_client_secret
        raise SharePointOutError(
            "identifiants Azure absents (CERT_THUMBPRINT + CERT_PRIVATE_KEY_PATH, ou CLIENT_SECRET)"
        )

    def _msal_token(self) -> str:
        s = self._s
        if not (s.azure_tenant_id and s.azure_client_id):
            raise SharePointOutError("TENANT_ID / CLIENT_ID absents")
        if self._msal_app is None:
            self._msal_app = msal.ConfidentialClientApplication(
                s.azure_client_id,
                authority=f"https://login.microsoftonline.com/{s.azure_tenant_id}",
                client_credential=self._credential(),
            )
        result = self._msal_app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" not in result:
            raise SharePointOutError(
                f"authentification Azure refusée : {result.get('error_description')}"
            )
        return result["access_token"]

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    # ── Site / bibliothèque ─────────────────────────────────────────────────
    def _check_config(self) -> None:
        if not self._s.sharepoint_out_host or not self._s.sharepoint_out_site_path.strip("/"):
            raise SharePointOutError(
                "SHAREPOINT_OUT_HOST (ou SHAREPOINT_HOST) / SHAREPOINT_OUT_SITE_PATH non renseignés"
            )

    def _resolve_drive_id(self) -> str:
        if self._drive_id:
            return self._drive_id
        self._check_config()
        site_path = "/" + self._s.sharepoint_out_site_path.strip("/")
        resp = self._http.get(
            f"{GRAPH_URL}/sites/{self._s.sharepoint_out_host}:{quote(site_path)}",
            headers=self._auth(),
        )
        if not resp.is_success:
            raise _graph_error(resp)
        site_id = resp.json()["id"]
        resp = self._http.get(f"{GRAPH_URL}/sites/{site_id}/drive", headers=self._auth())
        if not resp.is_success:
            raise _graph_error(resp)
        self._drive_id = resp.json()["id"]
        return self._drive_id

    # ── Dépôt ───────────────────────────────────────────────────────────────
    def upload(self, path: Path) -> str:
        """Dépose le fichier dans <dossier Out>/<nom> et retourne son URL SharePoint.

        Raises:
            FileAlreadyExistsError: un fichier du même nom est déjà présent (jamais écrasé).
            SharePointOutError: tout autre échec.
        """
        drive_id = self._resolve_drive_id()
        folder = self._s.sharepoint_out_folder.strip("/")
        item_path = quote(f"{folder}/{path.name}" if folder else path.name)
        data = path.read_bytes()

        if len(data) <= SIMPLE_UPLOAD_MAX_BYTES:
            resp = self._http.put(
                f"{GRAPH_URL}/drives/{drive_id}/root:/{item_path}:/content",
                params={"@microsoft.graph.conflictBehavior": "fail"},
                headers={**self._auth(), "Content-Type": "application/octet-stream"},
                content=data,
            )
            return self._finish(resp, path.name)

        session = self._http.post(
            f"{GRAPH_URL}/drives/{drive_id}/root:/{item_path}:/createUploadSession",
            headers=self._auth(),
            json={"item": {"@microsoft.graph.conflictBehavior": "fail", "name": path.name}},
        )
        if session.status_code == 409:
            raise FileAlreadyExistsError(path.name)
        if not session.is_success:
            raise _graph_error(session)
        upload_url = session.json()["uploadUrl"]
        total, start = len(data), 0
        resp: httpx.Response | None = None
        while start < total:
            end = min(start + CHUNK_BYTES, total)
            # l'URL de session est pré-authentifiée : pas d'en-tête Authorization
            resp = self._http.put(
                upload_url,
                content=data[start:end],
                headers={
                    "Content-Length": str(end - start),
                    "Content-Range": f"bytes {start}-{end - 1}/{total}",
                },
            )
            if resp.status_code == 409:
                raise FileAlreadyExistsError(path.name)
            if not resp.is_success:
                raise _graph_error(resp)
            start = end
        assert resp is not None
        return self._finish(resp, path.name)

    @staticmethod
    def _finish(resp: httpx.Response, name: str) -> str:
        if resp.status_code == 409:
            raise FileAlreadyExistsError(name)
        if not resp.is_success:
            raise _graph_error(resp)
        return resp.json().get("webUrl", "")


_client: SharePointOutClient | None = None


def _get_client() -> SharePointOutClient:
    global _client
    if _client is None:
        _client = SharePointOutClient(get_settings())
    return _client


def _deliver_sync(path: Path) -> DeliveryResult:
    # Best-effort : toute erreur est ramenée à un DeliveryResult, un dépôt raté ne doit
    # jamais interrompre le traitement du fichier fournisseur (comme l'archivage GCS).
    try:
        return DeliveryResult(ok=True, web_url=_get_client().upload(path))
    except FileAlreadyExistsError:
        return DeliveryResult(
            ok=False, error=f"{path.name} existe déjà dans le dossier de destination (non écrasé)"
        )
    except Exception as exc:  # noqa: BLE001
        return DeliveryResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:500])


async def deliver_file(path: Path) -> DeliveryResult:
    """Dépose un fichier d'export dans SharePoint (dossier Out) sans bloquer la boucle async."""
    return await asyncio.to_thread(_deliver_sync, path)
