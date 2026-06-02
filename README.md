# Middleware Ramery — Normalisation catalogues fournisseurs

Middleware de normalisation adaptative des catalogues Excel hétérogènes des fournisseurs Ramery vers l'ERP Gery (Microsoft Dynamics).

## Architecture

```
SharePoint Tempo (fournisseurs)
    ↓  Microsoft Graph /delta
n8n self-hosted (orchestration, scheduling 15 min)
    ↓
MinIO / GCS (archivage immuable des fichiers bruts)
    ↓
Service Python FastAPI (parsing YAML + pivot + delta + export)
    ↓
PostgreSQL (pivot canonique, historique, audit)
    ↓
3 fichiers Excel → dossier de sortie surveillé par Gery
  ├── NEW ARTICLE (création article, 19 colonnes)
  ├── NEW ART-FRNS création (liaison article-fournisseur, 8 colonnes)
  └── NEW ART-FRNS MAJ prix (mise à jour prix, 8 colonnes)
```

## Démarrage rapide

### Prérequis
- Docker + Docker Compose
- Python 3.12 + uv (`pip install uv`)

### 1. Configuration
```bash
cp .env.example .env
# Les valeurs par défaut fonctionnent pour le dev local
```

### 2. Lancer la stack complète
```bash
docker compose up -d
```

Services démarrés :
| Service | URL |
|---|---|
| API FastAPI | http://localhost:8000 |
| Docs Swagger | http://localhost:8000/docs |
| MinIO Console | http://localhost:9001 (admin/minioadmin) |
| n8n | http://localhost:5679 (admin/changeme) |
| PostgreSQL | localhost:5432 (middleware/middleware) |

### 3. Vérifier le démarrage
```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

### 4. Lancer les migrations (Livrable 2)
```bash
make db-migrate
```

## Développement local

### Installation
```bash
uv pip install -e ".[dev]"
```

### Commandes utiles
```bash
make test        # tests avec coverage
make lint        # ruff
make typecheck   # mypy
make run         # serveur local avec reload
make logs        # logs du container api
```

## Structure du projet

```
middleware-ramery/
├── src/middleware/
│   ├── api/           # Routes FastAPI
│   │   └── routes/    # Endpoints par domaine
│   ├── core/          # Config, logging, exceptions
│   ├── db/            # Modèles SQLAlchemy + session (Livrable 2)
│   ├── parser/        # Moteur d'extraction Excel (Livrable 4-6)
│   ├── delta/         # Calcul de delta (Livrable 7)
│   └── exporter/      # Génération fichiers Gery (Livrable 8)
├── config/
│   ├── suppliers/     # YAML de mapping par fournisseur
│   └── n8n/           # Workflows n8n exportés en JSON
├── alembic/           # Migrations base de données
├── tests/             # Tests pytest
├── data/
│   ├── input/         # Dossier de dépôt simulé (dev)
│   └── output/        # Fichiers Gery générés (dev)
└── docs/              # Documentation technique
```

## Fournisseurs pilotes

| Fournisseur | Mode d'extraction | Complexité | Export Gery |
|---|---|---|---|
| Atlantic SCGA — Chauffage | `table` | Simple | ✅ |
| Atlantic SCGA — Eau chaude | `table` | Simple | ✅ |
| Airisol | `matrix` (paliers × variantes) | Complexe | ✅ (stratégie cartésienne) |
| Agenor | `multi_table` (prestations) | Spécifique | ❌ (désactivé — arbitrage métier) |

## Notes de déploiement

- **Dev** : MinIO remplace GCS pour le stockage objet.
- **Prod** : remplacer `MIDDLEWARE_STORAGE_ENDPOINT` par l'endpoint GCS, utiliser les credentials GCP.
- **Secrets** : HashiCorp Vault AppRole en prod. `.env` uniquement en dev.
- **CI/CD** : GitLab CI (`.gitlab-ci.yml` à créer selon pipeline Ramery).

## Plan de livraison

- [x] Livrable 1 — Squelette projet + infra Docker dev
- [ ] Livrable 2 — Migrations Alembic + modèles SQLAlchemy
- [ ] Livrable 3 — Modèles Pydantic pivot + grammaire YAML
- [ ] Livrable 4 — Moteur extraction `table` (Atlantic)
- [ ] Livrable 5 — Moteur extraction `matrix` (Airisol)
- [ ] Livrable 6 — Moteur extraction `multi_table` (Agenor)
- [ ] Livrable 7 — Calcul delta + historique
- [ ] Livrable 8 — Générateur 3 fichiers Gery
- [ ] Livrable 9 — API FastAPI + workflow n8n
- [ ] Livrable 10 — Tests E2E + runbook
