# Certificat Azure AD (auth sans mot de passe) — guide reproductible

Remplace l'auth par `client secret` (mot de passe) par une auth par **certificat**,
exigée par la gouvernance cloud Ramery. Validé bout en bout sur le tenant MaikHub
(app `lecture_fichier`, celle utilisée par le watcher de ce repo) le 2026-09-25.

But de ce doc : que tu puisses refaire **toute la manip toi-même**, de zéro, sans
moi — que ce soit pour régénérer un certificat côté MaikHub, ou pour reproduire
exactement la même chose côté Ramery (Dominique fera l'étape Azure lui-même, mais
le reste — génération, test, Vault — c'est nous).

---

## 0. Les 3 fichiers qu'on génère — à quoi ils servent

| Fichier | Contenu | Va où |
|---|---|---|
| `xxx.key` | Clé privée RSA | **Reste toujours chez nous.** Jamais transmis (pas de mail, Slack, Teams). Vault uniquement. |
| `xxx.cer` | Certificat public | Pas sensible. Uploadé sur Azure AD (MaikHub pour test, Ramery en prod), et transmis à Sani. |
| `xxx.pfx` | `.key` + `.cer` combinés, protégés par mot de passe | Backup Vault uniquement, même sensibilité que `.key`. |

`.key` sert à **signer** la preuve d'identité envoyée à Azure AD. `.cer` sert à
**vérifier** cette signature côté Azure — c'est pour ça qu'il peut être public.

**Pourquoi le même certificat sert à la fois pour MaikHub (test) et Ramery
(prod)** : un certificat n'appartient à aucun tenant Azure AD. C'est un objet qu'on
fabrique nous-mêmes (auto-signé — pas d'autorité externe qui le "délivre"). Chaque
tenant Azure AD tient sa propre liste de certificats de confiance, *par app
registration* — uploader notre `.cer` sur l'app MaikHub (`lecture_fichier`) et sur
l'app Ramery (créée par Dominique) sont deux actions **totalement indépendantes**,
les deux tenants ne se parlent pas entre eux. Ce qui les relie, c'est qu'on détient
la **même clé privée** des deux côtés — donc on peut prouver notre identité aux
deux endroits avec un seul fichier `.key`, sans en regénérer un différent pour
chacun.

Ce qui change entre le test MaikHub et la vraie prod Ramery, ce n'est **pas** le
certificat — c'est uniquement `TENANT_ID`/`CLIENT_ID` (ils disent "quel tenant,
quelle app", pas des secrets, juste des identifiants). Ceux de Ramery seront donnés
par Dominique une fois qu'il aura créé son app et uploadé notre `.cer` de son côté.

---

## 1. Générer le certificat (OpenSSL)

Depuis un terminal Linux/Mac/Git Bash. Utiliser un dossier de travail dédié :

```bash
mkdir -p ~/tmp/ramery-cert && cd ~/tmp/ramery-cert

# Clé privée RSA 2048
openssl genrsa -out maikhub-middleware-ramery.key 2048

# Certificat auto-signé, valide 365 jours
# ⚠️ Sur Windows Git Bash : préfixer avec MSYS_NO_PATHCONV=1, sinon le "/CN=..."
# est mal interprété comme un chemin de fichier (bug connu Git Bash / MSYS).
MSYS_NO_PATHCONV=1 openssl req -new -x509 \
  -key maikhub-middleware-ramery.key \
  -out maikhub-middleware-ramery.cer \
  -days 365 \
  -subj "/CN=MaikHub-Middleware-CataloguesFournisseurs/O=MaikHub/C=FR"

# Bundle PFX chiffré (backup Vault) — CHANGER le mot de passe, ne pas garder l'exemple
openssl pkcs12 -export \
  -out maikhub-middleware-ramery.pfx \
  -inkey maikhub-middleware-ramery.key \
  -in maikhub-middleware-ramery.cer \
  -passout pass:CHOISIR_UN_VRAI_MOT_DE_PASSE

# Thumbprint SHA-1 (sera redemandé partout)
openssl x509 -in maikhub-middleware-ramery.cer -noout -fingerprint -sha1
```

**Vérif rapide du certificat généré** (CN + dates) :
```bash
openssl x509 -in maikhub-middleware-ramery.cer -noout -subject -dates
```

✅ Étape validée le 2026-09-25. Résultat obtenu :
- Thumbprint : `46AEE77C314281D43BFD479778A2B1466645A331`
- Validité : `25/09/2026` → `25/09/2027`

---

## 2. Upload sur Azure AD (portail, à la main)

1. https://portal.azure.com → **Azure Active Directory → App registrations**
2. Ouvrir l'app cible :
   - **MaikHub (test)** : app `lecture_fichier`, `CLIENT_ID=4c8fed44-2a06-44cb-b371-932fcd764d3e`,
     `TENANT_ID=1a0f9db2-ab9b-45fa-a9db-a03186b50e5e` (celle utilisée par le watcher
     de ce repo — voir `.env`). Ajouter un certificat dessus est **sans risque** :
     Azure AD accepte plusieurs credentials (secret + certificat) en parallèle, le
     watcher continue de tourner normalement avec le secret pendant qu'on teste.
   - **Ramery (prod)** : app créée par Dominique côté Ramery — c'est lui qui fait
     l'upload de son côté avec le `.cer` qu'on lui transmet (voir §6).
3. Menu de gauche → **Certificates & secrets → onglet "Certificates" → Upload certificate**
4. Sélectionner le fichier `.cer` (le déposer quelque part d'accessible avant, ex.
   Bureau — c'est un fichier public, pas besoin de précaution).
5. Après upload, **vérifier** :
   - Le thumbprint affiché par Azure = celui obtenu à l'étape 1 (openssl).
   - La date d'expiration = J+365 depuis la génération.
   - **Si ça ne correspond pas → s'arrêter, mauvais fichier, refaire.**

✅ Étape validée le 2026-09-25 — thumbprint et date confirmés identiques.

**⚠️ Piège rencontré : `AADSTS700027 - certificate not found`**
Un certificat fraîchement uploadé peut mettre **quelques minutes à se propager**
sur les serveurs d'auth Azure AD (contrairement à un secret, souvent immédiat).
Si le test de l'étape 3 échoue juste après l'upload avec cette erreur, **attendre
quelques minutes et réessayer** avant de chercher un autre problème — la config
était bonne dans notre cas.

---

## 3. Test Python MSAL (auth cert → token → appel Graph)

### Installer les dépendances (environnement isolé, pas dans le venv du projet)
```bash
python -m venv ~/tmp/ramery-cert/venv
source ~/tmp/ramery-cert/venv/bin/activate      # Linux/Mac
# ~/tmp/ramery-cert/venv/Scripts/activate       # Windows Git Bash
pip install msal requests
```
*(Pas ajouté au `pyproject.toml`/`uv.lock` du projet — c'est un outil de test
ponctuel, pas une dépendance de l'app.)*

### Script `test_cert_auth.py` (à créer dans `~/tmp/ramery-cert/`)
```python
import msal
import requests

# --- À adapter selon le tenant testé ---
TENANT_ID = "1a0f9db2-ab9b-45fa-a9db-a03186b50e5e"   # MaikHub (voir .env du repo)
CLIENT_ID = "4c8fed44-2a06-44cb-b371-932fcd764d3e"    # app "lecture_fichier"
KEY_PATH = "./maikhub-middleware-ramery.key"
THUMBPRINT = "46:AE:E7:7C:31:42:81:D4:3B:FD:47:97:78:A2:B1:46:66:45:A3:31"

thumbprint_clean = THUMBPRINT.replace(":", "")

with open(KEY_PATH, "r") as f:
    private_key = f.read()

app = msal.ConfidentialClientApplication(
    client_id=CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    client_credential={
        "thumbprint": thumbprint_clean,
        "private_key": private_key,
    },
)

result = app.acquire_token_for_client(
    scopes=["https://graph.microsoft.com/.default"]
)

if "access_token" in result:
    print("Token obtenu")
    print(f"  Expire dans {result['expires_in']}s")
    token = result["access_token"]
else:
    print("Echec authentification")
    print(f"  Error: {result.get('error')}")
    print(f"  Description: {result.get('error_description')}")
    exit(1)

headers = {"Authorization": f"Bearer {token}"}
r = requests.get("https://graph.microsoft.com/v1.0/sites/root", headers=headers)

if r.status_code == 200:
    print("Appel Graph reussi")
    site_data = r.json()
    print(f"  Site: {site_data.get('displayName')} ({site_data.get('webUrl')})")
else:
    print(f"Appel Graph echoue : HTTP {r.status_code}")
    print(f"  {r.text}")
```

### Exécution
```bash
cd ~/tmp/ramery-cert
python test_cert_auth.py
```

### Résultat attendu
```
Token obtenu
  Expire dans 3599s
Appel Graph reussi
  Site: <nom du site> (https://maikhub.sharepoint.com)
```

✅ Étape validée le 2026-09-25 :
```
Token obtenu
  Expire dans 3599s
Appel Graph reussi
  Site: Site de communication (https://maikhub.sharepoint.com)
```

---

## 4. Test spécifique Sites.Selected (site précis, pas `/sites/root`)

Objectif : prouver que le certificat marche aussi avec les permissions restreintes
réellement utilisées en prod (`Sites.Selected` sur `/sites/ref-fournisseur`, pas
juste l'appel générique `/sites/root` du §3).

Même token qu'en §3 (le certificat ne change rien aux permissions accordées —
elles sont liées à l'app, pas au credential utilisé pour s'authentifier). On
reproduit le pattern exact du watcher (`watcher/sharepoint_client.py::get_site_id`) :
résoudre le site par chemin, puis lister le drive.

### Script `test_cert_auth_sites_selected.py` (dans `~/tmp/ramery-cert/`)
```python
import msal
import requests

TENANT_ID = "1a0f9db2-ab9b-45fa-a9db-a03186b50e5e"
CLIENT_ID = "4c8fed44-2a06-44cb-b371-932fcd764d3e"
KEY_PATH = "./maikhub-middleware-ramery.key"
THUMBPRINT = "46:AE:E7:7C:31:42:81:D4:3B:FD:47:97:78:A2:B1:46:66:45:A3:31"

SHAREPOINT_HOST = "maikhub.sharepoint.com"
SHAREPOINT_SITE_PATH = "/sites/ref-fournisseur"
GRAPH_URL = "https://graph.microsoft.com/v1.0"

thumbprint_clean = THUMBPRINT.replace(":", "")
with open(KEY_PATH, "r") as f:
    private_key = f.read()

app = msal.ConfidentialClientApplication(
    client_id=CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    client_credential={"thumbprint": thumbprint_clean, "private_key": private_key},
)
result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
if "access_token" not in result:
    print("Echec authentification :", result.get("error_description"))
    exit(1)

headers = {"Authorization": f"Bearer {result['access_token']}"}

# 1. Resoudre le site_id du site restreint (Sites.Selected)
path = SHAREPOINT_SITE_PATH.strip("/")
r = requests.get(f"{GRAPH_URL}/sites/{SHAREPOINT_HOST}:/{path}", headers=headers)
r.raise_for_status()
site_data = r.json()
site_id = site_data["id"]
print(f"Site resolu : {site_data.get('displayName')} (id={site_id})")

# 2. Lister le contenu du drive
r = requests.get(f"{GRAPH_URL}/sites/{site_id}/drives", headers=headers)
r.raise_for_status()
drive_id = r.json()["value"][0]["id"]

r = requests.get(f"{GRAPH_URL}/drives/{drive_id}/root/children", headers=headers)
r.raise_for_status()
items = r.json().get("value", [])
print(f"Appel Graph Sites.Selected reussi ({len(items)} element(s) a la racine) :")
for item in items:
    print(f"  - {item.get('name')}")
```

### Exécution
```bash
cd ~/tmp/ramery-cert
python test_cert_auth_sites_selected.py
```

✅ Étape validée le 2026-09-25 :
```
Token obtenu (certificat)
Site resolu : ref-fournisseur (id=maikhub.sharepoint.com,cce4ad2d-a7fa-4cad-ba5b-03d1ee220d5d,489b8355-d9fd-4852-afd6-b5b9ce60c335)
Appel Graph Sites.Selected reussi (13 element(s) a la racine) :
  - airisol
  - Alkern
  - Apok
  - Atlantic
  - BUSCA DISTRIBUTION (SOCCA TP)
  - DOCKS DE L'OISE (POINT P)
  - PUM
  - PUM 2
  - RG FRANCE (FIPROTEC)
  - SOGEDESCA (PROLIANS)
  - test wael
  - test wael 2
  - TRENOIS DECAMPS SETIN
```
→ Flow complet certificat + Sites.Selected validé sur le scénario réel de
production. C'est exactement ce que Dominique rejouera côté Ramery.

---

## 5. Stockage Vault

**⏳ Pas encore fait** — nécessite l'accès Vault (pas disponible dans une session
Claude Code standard, à faire toi-même).

Chemin suggéré : `secret/ramery/middleware/azure-auth/` — voir le détail complet
(clé par clé) dans le mail original de Sani (mai 2026), section "Étape 5".

**Après stockage Vault, nettoyer les fichiers locaux :**
```bash
cd ~/tmp
shred -u ramery-cert/*.key ramery-cert/*.pfx
rm -rf ramery-cert/
```
Ne garder le `.cer` que le temps de le transmettre à Sani (§6).

---

## 6. Transmission à Sani

À envoyer :
- Le fichier `.cer` (canal quelconque, pas sensible).
- Le thumbprint SHA-1 : `46AEE77C314281D43BFD479778A2B1466645A331`
- Confirmation étapes 3 et 4 OK.
- Avis sur la durée de validité (1 an par défaut, sauf contrainte Ramery vue ailleurs).

---

## 7. Certificat de production — généré directement sur la machine de prod (GCP)

**Contexte différent du reste de ce doc** : plutôt que de réutiliser le certificat
MaikHub pour la vraie prod Ramery, décision prise le 2026-09-28 de **générer un
certificat neuf directement sur la machine de prod** (`srv-maikhub-gcp-ramery-prod`,
GCP, connectée via le SSH intégré du navigateur dans la Cloud Console — pas de
`gcloud` CLI ni de nom DNS public résolvable depuis un poste externe). Raison :
la clé privée naît directement là où elle va vivre, aucune copie/transfert du
`.key` nécessaire. Autre changement de process : ce n'est plus Dominique qui gère
l'étape Azure via Sani, mais **un développeur interne côté Ramery** qui reprend le
process à partir d'ici — on lui fournit le certificat + les instructions, il fait
le reste côté Azure (voir checklist plus bas).

Mêmes commandes qu'au §1, lancées directement sur la machine de prod (pas de souci
`MSYS_NO_PATHCONV` — c'est du Linux natif, pas Git Bash Windows) :
```bash
mkdir -p ~/tmp/cert && cd ~/tmp/cert
openssl genrsa -out maikhub-middleware-ramery.key 2048
openssl req -new -x509 \
  -key maikhub-middleware-ramery.key \
  -out maikhub-middleware-ramery.cer \
  -days 365 \
  -subj "/CN=MaikHub-Middleware-CataloguesFournisseurs/O=MaikHub/C=FR"
openssl x509 -in maikhub-middleware-ramery.cer -noout -fingerprint -sha1
```

✅ Étape validée le 2026-09-28. Résultat obtenu :
- Thumbprint : `5FA0DF30C112F2A353DD7C2B070DBBBF47E599F9`
- Validité : `28/09/2026` → `28/09/2027`
- Subject vérifié : `CN=MaikHub-Middleware-CataloguesFournisseurs, O=MaikHub, C=FR` ✅

**Téléchargement du `.cer` en local** (pas de `scp` classique possible, hostname
interne non résolvable) — utiliser la fonction intégrée du terminal SSH-dans-le-
navigateur de la Cloud Console GCP : icône **⚙️ en haut à droite → "Download
file"**, coller le chemin **absolu** du fichier (obtenu via `realpath
~/tmp/cert/maikhub-middleware-ramery.cer`). Télécharge dans le dossier
Téléchargements Windows.

**⚠️ À faire avant de considérer cette étape terminée** (pas encore fait au
2026-09-28) :
- Déplacer `.key`/`.pfx` hors de `~/tmp/` (répertoire temporaire, potentiellement
  purgé) vers un emplacement permanent, ex. `~/middleware-ramery/certs/` avec
  permissions `700`/`600` comme sur le VPS de test (voir §2 du reste du doc pour le
  pattern).
- Déployer le code du watcher (support certificat, déjà sur `origin/main` depuis le
  commit `1d45b9d`) sur cette machine de prod.
- Ajouter `CERT_THUMBPRINT=5FA0DF30C112F2A353DD7C2B070DBBBF47E599F9` dans le `.env`
  de cette machine, une fois `TENANT_ID`/`CLIENT_ID` de Ramery connus (voir
  checklist ci-dessous).

### Ce qui doit rester uniquement sur cette machine (jamais transmis)
- `maikhub-middleware-ramery.key`
- `maikhub-middleware-ramery.pfx` (si créé) + son mot de passe (stocké séparément,
  ex. gestionnaire de mots de passe — jamais dans un fichier texte ni dans git)

### Ce qu'on transmet au développeur interne Ramery
- `maikhub-middleware-ramery.cer` (fichier, pas sensible)
- Le thumbprint `5FA0DF30C112F2A353DD7C2B070DBBBF47E599F9` (pas indispensable
  techniquement — Azure le recalcule lui-même à l'upload — mais sert de
  vérification d'intégrité : si le thumbprint qu'Azure affiche après son upload ne
  correspond pas à celui-ci, mauvais fichier / transfert corrompu, à refaire)
- Instructions : créer/utiliser une app registration côté tenant Ramery → uploader
  ce `.cer` (Certificates & secrets → Certificates → Upload certificate) →
  vérifier thumbprint identique → s'assurer que `Sites.Selected` est accordé sur
  le site SharePoint concerné.

### Ce qu'il doit renvoyer, pour finaliser la config du watcher
- `TENANT_ID` (tenant Ramery)
- `CLIENT_ID` (app créée/utilisée côté Ramery)

---

## Repères rapides

| Info | Valeur |
|---|---|
| Tenant MaikHub | `1a0f9db2-ab9b-45fa-a9db-a03186b50e5e` |
| App test (MaikHub) | `lecture_fichier` / `4c8fed44-2a06-44cb-b371-932fcd764d3e` |
| Thumbprint cert MaikHub (test) | `46AEE77C314281D43BFD479778A2B1466645A331` |
| Expiration cert MaikHub | `25/09/2027` |
| Site SharePoint testé (MaikHub) | `/sites/ref-fournisseur` (voir `.env` → `SHAREPOINT_SITE_PATH`) |
| Machine de prod | `srv-maikhub-gcp-ramery-prod` (GCP, SSH via Cloud Console) |
| Thumbprint cert prod | `5FA0DF30C112F2A353DD7C2B070DBBBF47E599F9` |
| Expiration cert prod | `28/09/2027` |
| `TENANT_ID`/`CLIENT_ID` Ramery | ⏳ en attente du développeur interne Ramery |
