# Déploiement sur une nouvelle VM

Guide pour déployer la stack middleware-ramery (API, watcher SharePoint, PostgreSQL, MinIO, n8n) sur une VM neuve (Ubuntu/Debian).

## 1. Prérequis sur la VM

- Docker + Docker Compose plugin
- Git
- Accès réseau sortant vers `graph.microsoft.com` (watcher) et vers le tenant SharePoint

```bash
# Installation Docker (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# se reconnecter pour appliquer le groupe docker
```

## 2. Cloner le repo

```bash
git clone https://github.com/waelbouzoubaa/referentiel.git middleware-ramery
cd middleware-ramery
```

## 3. Configuration `.env`

```bash
cp .env.example .env
```

Variables à **renseigner obligatoirement** (le reste fonctionne avec les valeurs par défaut en dev) :

| Variable | Description |
|---|---|
| `TENANT_ID` | ID du tenant Azure AD (Microsoft Entra) |
| `CLIENT_ID` | ID de l'app registration Azure AD |
| `CLIENT_SECRET` | Secret de cette app registration |
| `SHAREPOINT_HOST` | Domaine SharePoint (ex: `maikhub.sharepoint.com`) |
| `SHAREPOINT_SITE_PATH` | Chemin du site (ex: `/sites/test2`) |
| `GEMINI_API_KEY` | Clé API Gemini (analyse IA des fournisseurs inconnus) |

L'app registration Azure AD doit avoir les permissions Microsoft Graph **application** suivantes (consentement admin) : `Sites.Read.All`, `Files.Read.All`.

⚠️ **Ne jamais commiter `.env`** (déjà dans `.gitignore`). Pour les credentials de prod, voir la section "Notes de déploiement" du `README.md` (Vault).

## 4. Build et démarrage de la stack

```bash
docker compose build
docker compose up -d
```

Services démarrés :

| Service | URL / Port | Identifiants |
|---|---|---|
| API FastAPI | `http://<vm>:8000` (docs : `/docs`) | — |
| Validation mappings IA (Streamlit) | `http://<vm>:8503` | — |
| MinIO Console | `http://<vm>:9011` | `minioadmin` / `minioadmin` |
| MinIO API S3 | `http://<vm>:9010` | `minioadmin` / `minioadmin` |
| n8n | `http://<vm>:5679` | `admin` / `changeme` (via `N8N_BASIC_AUTH_*`) |
| PostgreSQL | `<vm>:5432` | `middleware` / `middleware` |

> ⚠️ Pour une VM accessible publiquement, changer les mots de passe par défaut (Postgres, MinIO, n8n) dans `docker-compose.yml` / `.env`, et restreindre les ports exposés via le pare-feu (seul `8000` et éventuellement `9011`/`5679` doivent être accessibles depuis l'extérieur si besoin).

## 5. Migrations base de données

```bash
docker compose exec api alembic upgrade head
```

Vérifier :
```bash
docker compose exec postgres psql -U middleware -d middleware -c "\dt"
```
→ doit lister les 14 tables (`suppliers`, `products`, `prices`, etc.)

## 6. Vérification du démarrage

```bash
curl http://localhost:8000/health
# {"status":"ok",...}

curl http://localhost:8000/suppliers/folder-mapping
# doit lister les fournisseurs définis dans config/suppliers/*.yaml
```

## 7. Configuration des fournisseurs

Les fournisseurs sont définis par les fichiers `config/suppliers/*.yaml` (déjà dans le repo : `airisol_v1.yaml`, `agenor_v1.yaml`, `atlantic_scga_chauffage_v1.yaml`, `atlantic_scga_eau_v1.yaml`).

Chaque YAML porte un champ `sharepoint_folder` qui indique le(s) nom(s) de dossier SharePoint surveillé(s) — le watcher les récupère dynamiquement via `GET /suppliers/folder-mapping`. **Ajouter un nouveau fournisseur ne nécessite donc qu'un nouveau fichier YAML**, pas de modification de code.

## 8. Démarrage du watcher

Le watcher démarre automatiquement avec `docker compose up -d` (service `watcher`, `restart: unless-stopped`).

Au premier démarrage, il fait un scan complet du drive SharePoint (peut prendre du temps selon le volume). L'état (delta token, cache fichiers) est persisté dans le volume `watcher_state`.

```bash
docker compose logs -f watcher
```

## 9. Commandes utiles

**Recharger le code Python (api)** — `./src` est monté en volume :
```bash
docker compose restart api
```

**Recharger le watcher** — `./watcher` n'est PAS monté en volume, rebuild requis :
```bash
docker compose up -d --build watcher
```

**Recharger l'interface de validation des mappings IA** — `./streamlit_review` n'est PAS monté en volume, rebuild requis :
```bash
docker compose up -d --build review_ui
```

**Logs** :
```bash
docker compose logs -f api
docker compose logs -f watcher
```

**Reset complet de la base (tests)** :
```bash
docker compose exec postgres psql -U middleware -d middleware -c "
TRUNCATE TABLE
  suppliers, mapping_rules, supplier_files, products, product_variants,
  product_attributes, prices, commercial_rules, product_history,
  product_audit, gery_exports, gery_export_lines, mapping_suggestions,
  processing_errors
RESTART IDENTITY CASCADE;
"
docker compose exec api sh -c "rm -f /app/exports/*.xlsx"
```

**Accéder aux fichiers bruts archivés (MinIO)** :
- Console web : `http://<vm>:9011` (`minioadmin`/`minioadmin`), bucket `middleware-dev`
- CLI : `docker compose exec minio mc ls -r local/middleware-dev/`

**Exports Gery générés** :
```bash
docker compose exec api ls -la /app/exports
```

## 10. Fournisseur inconnu — validation IA du mapping

Quand le watcher détecte un fichier dans un dossier SharePoint non mappé, il l'envoie
à `POST /api/v1/ingest/unknown`, qui appelle Gemini pour proposer un YAML de mapping
(`config/suppliers/`). La proposition est mise en attente (`/app/uploads/pending/{id}.json`).

L'interface **Validation mappings IA** (`http://<vm>:8503`, service `review_ui`) permet de :
- relire un aperçu du fichier Excel source ;
- éditer le YAML proposé (onglet "YAML") ou via un formulaire simplifié (onglet "Formulaire
  simplifié", disponible pour `extraction_mode: table`) ;
- valider (sauvegarde le YAML dans `config/suppliers/` et génère les exports Gery) ou rejeter.

⚠️ Nécessite `GEMINI_API_KEY` renseigné dans `.env` — sans cette clé, un YAML placeholder
(à compléter manuellement) est proposé.

## 11. Points d'attention connus

- Le `Dockerfile` racine a été reconstruit (build multi-stage `uv` + Python 3.12, target `runtime`). À valider lors du premier `docker compose build` sur une VM neuve.
- **Phase 5 non implémentée** : les changements de prix détectés (`product_audit`, `field_name=price`) ne génèrent pas encore de fichier Gery `PRICE_CHANGE` (seul `NEW_ARTICLE` est exporté actuellement).
- **Agenor** : `gery_export.enabled: false` — aucun export Gery n'est généré pour ce fournisseur (arbitrage métier en attente).
