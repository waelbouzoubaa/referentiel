import msal
import requests
from config import (
    TENANT_ID,
    CLIENT_ID,
    CLIENT_SECRET,
    CERT_THUMBPRINT,
    CERT_PRIVATE_KEY_PATH,
    SHAREPOINT_HOST,
    SHAREPOINT_SITE_PATH,
)

GRAPH_URL = "https://graph.microsoft.com/v1.0"


def _client_credential():
    """Certificat si CERT_THUMBPRINT + CERT_PRIVATE_KEY_PATH sont renseignés,
    sinon repli sur CLIENT_SECRET (comportement historique inchangé)."""
    if CERT_THUMBPRINT and CERT_PRIVATE_KEY_PATH:
        with open(CERT_PRIVATE_KEY_PATH, "r") as f:
            private_key = f.read()
        return {"thumbprint": CERT_THUMBPRINT.replace(":", ""), "private_key": private_key}
    return CLIENT_SECRET


def get_token():
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=_client_credential(),
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise Exception(f"Erreur auth: {result.get('error_description')}")
    return result["access_token"]


def get_headers():
    return {"Authorization": f"Bearer {get_token()}"}


def get_site_id():
    path = SHAREPOINT_SITE_PATH.strip("/")
    url = f"{GRAPH_URL}/sites/{SHAREPOINT_HOST}:/{path}" if path else f"{GRAPH_URL}/sites/{SHAREPOINT_HOST}:/"
    resp = requests.get(url, headers=get_headers())
    resp.raise_for_status()
    return resp.json()["id"]


def get_drive_id(site_id):
    resp = requests.get(
        f"{GRAPH_URL}/sites/{site_id}/drives",
        headers=get_headers()
    )
    resp.raise_for_status()
    drives = resp.json()["value"]
    return drives[0]["id"]


def get_folder_id(drive_id, folder_path):
    """Résout l'ID d'un sous-dossier du drive à partir de son chemin
    (ex: 'Documents par Fournisseur'), pour restreindre le watcher à ce
    sous-dossier au lieu de toute la racine du drive."""
    path = folder_path.strip("/")
    resp = requests.get(
        f"{GRAPH_URL}/drives/{drive_id}/root:/{path}",
        headers=get_headers()
    )
    resp.raise_for_status()
    return resp.json()["id"]


def get_list_columns(drive_id):
    """Colonnes de la bibliothèque de documents associée au drive."""
    resp = requests.get(
        f"{GRAPH_URL}/drives/{drive_id}/list/columns",
        headers=get_headers()
    )
    resp.raise_for_status()
    return resp.json()["value"]


def get_item_fields(drive_id, item_id):
    """Valeurs des colonnes personnalisées (metadata) d'un fichier SharePoint."""
    resp = requests.get(
        f"{GRAPH_URL}/drives/{drive_id}/items/{item_id}/listItem?$expand=fields",
        headers=get_headers()
    )
    resp.raise_for_status()
    return resp.json().get("fields", {})
