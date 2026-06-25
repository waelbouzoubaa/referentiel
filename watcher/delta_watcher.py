import sys
import time
import json
import uuid
import requests
from pathlib import Path
from sharepoint_client import get_headers, get_site_id, get_drive_id
from config import POLL_INTERVAL, MIDDLEWARE_API_URL, UPLOADS_DIR

sys.stdout.reconfigure(encoding="utf-8")

GRAPH_URL = "https://graph.microsoft.com/v1.0"
DELTA_TOKEN_FILE = Path("delta_token.json")
FILE_CACHE_FILE = Path("file_cache.json")

# Mapping dossier SharePoint (minuscules) → liste de {supplier_code, filename_keywords}
# Mis à jour à chaque cycle de polling — voir _refresh_folder_mapping().
_folder_to_suppliers: dict[str, list[dict]] = {}


def _refresh_folder_mapping():
    """Recharge le mapping dossier→fournisseurs depuis le middleware."""
    global _folder_to_suppliers
    try:
        resp = requests.get(f"{MIDDLEWARE_API_URL}/suppliers/folder-mapping", timeout=15)
        resp.raise_for_status()
        _folder_to_suppliers = resp.json()
    except Exception as exc:
        if not _folder_to_suppliers:
            print(f"  → Impossible de charger le mapping fournisseurs ({exc})")


def load_state(drive_id):
    delta_link = None
    file_cache = {}
    if DELTA_TOKEN_FILE.exists():
        data = json.loads(DELTA_TOKEN_FILE.read_text())
        delta_link = data.get(drive_id)
    if FILE_CACHE_FILE.exists():
        file_cache = json.loads(FILE_CACHE_FILE.read_text())
    return delta_link, file_cache


def save_state(drive_id, delta_link, file_cache):
    data = {}
    if DELTA_TOKEN_FILE.exists():
        data = json.loads(DELTA_TOKEN_FILE.read_text())
    data[drive_id] = delta_link
    DELTA_TOKEN_FILE.write_text(json.dumps(data, indent=2))
    FILE_CACHE_FILE.write_text(json.dumps(file_cache, indent=2))


def fetch_delta(url):
    items = []
    next_link = url
    delta_link = None
    while next_link:
        resp = requests.get(next_link, headers=get_headers())
        resp.raise_for_status()
        body = resp.json()
        items.extend(body.get("value", []))
        next_link = body.get("@odata.nextLink")
        delta_link = body.get("@odata.deltaLink")
    return items, delta_link


def process_changes(items, file_cache):
    for item in items:
        item_id = item["id"]

        if "folder" in item:
            if "deleted" not in item:
                file_cache[item_id] = {"name": item["name"], "path": ""}
            continue

        if "deleted" in item:
            cached = file_cache.pop(item_id, {})
            label = cached.get("path") or cached.get("name") or item_id
            print(f"[SUPPRIME]  {label}")
            on_deleted(item, cached)
            continue

        name = item.get("name", "inconnu")
        parent_path = item.get("parentReference", {}).get("path", "")
        fournisseur = parent_path.split("root:")[-1].strip("/") if "root:" in parent_path else parent_path.split("/")[-1]
        full_path = f"{fournisseur}/{name}" if fournisseur else name

        file_cache[item_id] = {"name": name, "path": full_path}

        if item.get("lastModifiedDateTime") == item.get("createdDateTime"):
            print(f"[AJOUTE]    {full_path}")
            on_created(item)
        else:
            print(f"[MODIFIE]   {full_path}")
            on_updated(item)


def on_created(item):
    _trigger_middleware(item)


def on_updated(item):
    _trigger_middleware(item)


def on_deleted(item, cached):
    label = cached.get("path") or cached.get("name") or item["id"]
    print(f"  → Suppression '{label}' — à traiter manuellement dans Gery.")


def _trigger_middleware(item):
    name = item.get("name", "inconnu")

    if not name.lower().endswith((".xlsx", ".xls")):
        print(f"  → Ignoré (pas un fichier Excel) : {name}")
        return

    supplier_code = _resolve_supplier_code(item)
    if supplier_code is None:
        _handle_unknown_supplier(item)
        return

    print(f"  → Téléchargement '{name}' → fournisseur '{supplier_code}'...")

    try:
        file_bytes = _download_file(item)
    except Exception as exc:
        print(f"  → Erreur téléchargement : {exc}")
        return

    # Sauvegarde dans le volume partagé avec l'API
    uploads_dir = Path(UPLOADS_DIR)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(name).suffix
    tmp_path = uploads_dir / f"{uuid.uuid4().hex}{suffix}"
    tmp_path.write_bytes(file_bytes)

    print(f"  → Appel middleware : POST {MIDDLEWARE_API_URL}/api/v1/generate-gery-exports")

    try:
        resp = requests.post(
            f"{MIDDLEWARE_API_URL}/api/v1/generate-gery-exports",
            json={
                "supplier_code": supplier_code,
                "file_path": str(tmp_path),
                "output_dir": "/app/exports",
                "original_filename": name,
                "sharepoint_item_id": item.get("id"),
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("anomaly_detected"):
            print(f"  → ⚠️  ANOMALIE DÉTECTÉE — routé vers validation manuelle (ID: {data.get('anomaly_pending_id')})")
            for issue in data.get("anomaly_issues", []):
                print(f"     ! {issue}")
        else:
            print(f"  → OK : {len(data.get('files', []))} fichier(s) Gery généré(s).")
            for f in data.get("files", []):
                print(f"     • {f['kind']} — {f['line_count']} ligne(s)")
    except requests.HTTPError as exc:
        print(f"  → Erreur HTTP {exc.response.status_code} : {exc.response.text[:300]}")
    except requests.ConnectionError:
        print(f"  → Impossible de joindre le middleware ({MIDDLEWARE_API_URL})")
    except Exception as exc:
        print(f"  → Erreur : {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)


def _download_file(item) -> bytes:
    download_url = item.get("@microsoft.graph.downloadUrl")
    if download_url:
        resp = requests.get(download_url, timeout=60)
        resp.raise_for_status()
        return resp.content

    item_id = item["id"]
    drive_id = item.get("parentReference", {}).get("driveId")
    if not drive_id:
        raise ValueError("driveId introuvable dans l'item.")

    resp = requests.get(
        f"{GRAPH_URL}/drives/{drive_id}/items/{item_id}/content",
        headers=get_headers(),
        allow_redirects=True,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def _handle_unknown_supplier(item):
    """Fournisseur inconnu : télécharge le fichier et l'envoie pour analyse IA + validation humaine."""
    name = item.get("name", "inconnu")
    parent_path = item.get("parentReference", {}).get("path", "")
    if "root:" in parent_path:
        folder = parent_path.split("root:")[-1].strip("/").split("/")[-1]
    else:
        folder = parent_path.strip("/").split("/")[-1]

    print(f"  → Fournisseur inconnu pour '{name}' (dossier: '{folder}') — analyse IA en cours...")

    try:
        file_bytes = _download_file(item)
    except Exception as exc:
        print(f"  → Erreur téléchargement : {exc}")
        return

    pending_id = uuid.uuid4().hex
    suffix = Path(name).suffix
    pending_dir = Path(UPLOADS_DIR) / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    file_path = pending_dir / f"{pending_id}{suffix}"
    file_path.write_bytes(file_bytes)

    try:
        resp = requests.post(
            f"{MIDDLEWARE_API_URL}/api/v1/ingest/unknown",
            json={
                "filename": name,
                "folder_name": folder,
                "file_path": str(file_path),
                "pending_id": pending_id,
                "web_url": item.get("webUrl"),
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"  → Envoyé pour validation. Fournisseur suggéré : '{data['supplier_guess']}' (ID: {pending_id})")
    except Exception as exc:
        print(f"  → Erreur envoi analyse IA : {exc}")
        file_path.unlink(missing_ok=True)


def _resolve_supplier_code(item) -> str | None:
    """Résout le supplier_code pour un fichier SharePoint.

    Cherche dans le dossier du fichier tous les YAMLs candidats.
    Si plusieurs YAMLs existent pour ce dossier, utilise les filename_keywords
    pour choisir le bon. Sans keywords → s'applique à tous les fichiers du dossier.
    """
    name = item.get("name", "").lower()
    parent_path = item.get("parentReference", {}).get("path", "")
    if "root:" in parent_path:
        folder = parent_path.split("root:")[-1].strip("/").split("/")[-1]
    else:
        folder = parent_path.strip("/").split("/")[-1]

    candidates = _folder_to_suppliers.get(folder.lower(), [])
    if not candidates:
        return None

    # Pour chaque YAML candidat : si keywords définis → ils doivent matcher le nom du fichier
    #                              si pas de keywords → joker (s'applique à tout)
    matched_with_keywords = []
    fallbacks = []  # YAMLs sans keywords (jokers)

    for candidate in candidates:
        keywords = candidate.get("filename_keywords") or []
        if not keywords:
            fallbacks.append(candidate)
        elif any(kw.lower() in name for kw in keywords):
            matched_with_keywords.append(candidate)

    # Priorité : match par keyword > joker sans keyword
    if matched_with_keywords:
        return matched_with_keywords[0]["supplier_code"]
    if fallbacks:
        return fallbacks[0]["supplier_code"]

    # Aucun YAML ne correspond → fichier inconnu → validation manuelle
    return None


def run():
    print(f"Watcher démarré — middleware : {MIDDLEWARE_API_URL}")
    site_id = get_site_id()
    drive_id = get_drive_id(site_id)
    print(f"Drive ID : {drive_id}")

    delta_link, file_cache = load_state(drive_id)
    if not delta_link:
        print("Premier scan complet...")
        delta_link = f"{GRAPH_URL}/drives/{drive_id}/root/delta"

    while True:
        _refresh_folder_mapping()
        print(f"\nPolling delta...")
        items, new_delta_link = fetch_delta(delta_link)

        fichiers = [i for i in items if "folder" not in i]
        if fichiers:
            print(f"{len(fichiers)} changement(s) :")
            process_changes(items, file_cache)
        else:
            process_changes(items, file_cache)
            print("Aucun changement.")

        save_state(drive_id, new_delta_link, file_cache)
        delta_link = new_delta_link

        interval_str = f"{POLL_INTERVAL}s" if POLL_INTERVAL < 60 else f"{POLL_INTERVAL // 60} min"
        print(f"Prochain check dans {interval_str}...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
