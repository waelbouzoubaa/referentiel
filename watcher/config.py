from dotenv import load_dotenv
import os

load_dotenv()

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

SHAREPOINT_HOST = os.getenv("SHAREPOINT_HOST")
SHAREPOINT_SITE_PATH = os.getenv("SHAREPOINT_SITE_PATH", "/")

MIDDLEWARE_API_URL = os.getenv("MIDDLEWARE_API_URL", "http://api:8000")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))

# Colonne SharePoint (choix Oui/Non) qui indique qu'un fichier est prêt à être traité.
# Tant qu'elle vaut "Non" (ou est vide), le watcher ignore le fichier.
TAG_COLUMN_NAME = os.getenv("TAG_COLUMN_NAME", "Export Gery")
TAG_COLUMN_READY_VALUE = os.getenv("TAG_COLUMN_READY_VALUE", "Oui")

# Dossier partagé entre le watcher et l'API (volume Docker)
UPLOADS_DIR = os.getenv("UPLOADS_DIR", "/app/uploads")

# Dossier dédié à l'état persistant du watcher (delta_token.json, file_cache.json) —
# volume Docker séparé (`watcher_state`), survit aux rebuilds du conteneur.
STATE_DIR = os.getenv("STATE_DIR", "/app/state")
