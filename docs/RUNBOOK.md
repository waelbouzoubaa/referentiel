# Runbook opérationnel — Middleware catalogues fournisseurs → Gery

Guide pour **faire tourner et dépanner** le middleware **sans le développeur** (Wael).
Tout se passe sur le VPS. Les commandes sont à copier-coller.

---

## 0. L'essentiel en 30 secondes
- **À quoi ça sert** : lire les catalogues Excel fournisseurs (SharePoint), les normaliser, et produire des fichiers CSV d'import pour **Gery**.
- **Interface** : `http://72.60.189.114:8503` (validation des nouveaux fournisseurs + exports).
- **Le système tourne tout seul** : un fichier déposé dans un dossier **connu** est traité automatiquement. Un dossier **inconnu** demande une **validation** dans l'interface.
- **Rien ne se perd** : base PostgreSQL + fichiers bruts (MinIO) + exports (disque), tous persistés.

---

## 1. Se connecter au serveur
```bash
ssh wael@72.60.189.114
cd ~/middleware-ramery
```

| Service | Adresse |
|---|---|
| Interface de validation (Streamlit) | http://72.60.189.114:8503 |
| API (santé) | http://72.60.189.114:8000/health |
| Console MinIO (stockage brut) | http://72.60.189.114:9011 |

---

## 2. Vérifier que tout tourne
```bash
docker compose ps          # tous les conteneurs "Up" ; api "healthy"
curl http://localhost:8000/health     # -> {"status":"ok","version":"0.1.0"}
```
Conteneurs attendus : `middleware-api`, `middleware-review-ui`, `middleware-watcher`,
`middleware-postgres`, `middleware-minio`, `middleware-n8n`.

Si un conteneur est arrêté : `docker compose up -d`

---

## 3. Déployer une mise à jour (quand du code a été poussé sur GitHub)
```bash
cd ~/middleware-ramery
git pull origin main
docker compose up -d --build api review_ui watcher
docker compose ps           # vérifier que tout est reparti
```

**Si `git pull` est bloqué** par des fichiers modifiés sur la VM :
```bash
git checkout -- <le_fichier_indiqué>     # jette la version locale (GitHub fait foi)
git pull origin main
# ou, pour tout remettre d'un coup :
git stash && git pull origin main && git stash drop
```

**Si erreur `insufficient permission ... .git/objects`** (droits) :
```bash
sudo chown -R wael:wael ~/middleware-ramery
git pull origin main
```

---

## 4. Voir les logs (pour comprendre ce qui se passe)
```bash
docker compose logs --tail=50 watcher     # détection des fichiers SharePoint
docker compose logs --tail=50 api         # traitement / erreurs
docker compose logs --tail=50 review_ui   # interface
```
Dans les logs du watcher, on voit : `[AJOUTE]/[MODIFIE] <fichier> → OK : N fichier(s)`.
- `OK : 1 fichier(s)` = export généré.
- `OK : 0 fichier(s)` = rien de neuf (déjà traité) ou export désactivé (cas agenor) — **normal**.
- `Erreur HTTP 4xx/5xx` = problème de mapping → voir §7.

---

## 5. Opérations courantes

### a) Ajouter / valider un fournisseur
1. Un fichier d'un dossier **inconnu** apparaît dans l'interface, onglet **« En attente »**.
2. Ouvrir la demande : à droite l'aperçu Excel (grille A,B,C), à gauche l'édition.
3. Corriger le mapping (Formulaire, YAML, ou Assistant IA), vérifier avec **« Aperçu export Gery »**.
4. Cliquer **✅ Valider** → le fournisseur devient connu, ses prochains fichiers sont automatiques.

> Détail pour créer un mapping à la main : `docs/GUIDE_YAML_TABLE.md`.

### b) Récupérer les fichiers Gery générés
Interface → barre latérale → **Vue : « Exports Gery »** → liste des CSV + bouton de téléchargement.
(ou sur le serveur : `docker compose exec api ls -lah /app/exports`)

### c) Renseigner les codes fournisseurs SAGE (placeholder)
Éditer `config/sage_codes.csv` (format `code_fournisseur,code_sage`). Pris en compte au prochain export.

---

## 6. Repères : où sont les choses
| Quoi | Où |
|---|---|
| Mappings fournisseurs (YAML) | `config/suppliers/<code>_v1.yaml` |
| Codes SAGE (placeholder) | `config/sage_codes.csv` |
| Exports CSV générés | volume `exports` (`docker compose exec api ls /app/exports`) |
| Fichiers bruts archivés | MinIO (console `:9011`) |
| Base de données (pivot, historique) | PostgreSQL (volume `postgres_data`) |

---

## 7. Dépannage (problèmes fréquents)

**« Un fichier d'un dossier connu ne génère rien »**
- `OK : 0 fichier(s)` dans les logs = soit déjà traité (inchangé → normal), soit export désactivé (agenor).
- `Erreur HTTP 500/422` = souci de mapping. Cause la plus fréquente : **nom d'onglet**.
  Corriger le YAML du fournisseur (`config/suppliers/<code>_v1.yaml`) : mettre `sheet_match: "auto"`.
  Puis re-déclencher (modifier/redéposer le fichier).

**« Une demande en attente affiche un YAML vide »**
- Recharger la page du navigateur (Ctrl+Shift+R) — l'interface n'est pas à jour.

**« Repartir d'un environnement propre pour une démo/test »**
```bash
bash scripts/reset_test_env.sh --yes      # ⚠️ DESTRUCTIF : vide base, exports, pending, MinIO
```

**Revenir à la version précédente (rollback) si une mise à jour casse**
```bash
git log --oneline -5                       # repérer le commit précédent (SHA)
git checkout <SHA_precedent>
docker compose up -d --build api review_ui watcher
# pour revenir à la dernière version : git checkout main && docker compose up -d --build ...
```

**Redémarrer proprement toute la stack**
```bash
docker compose restart            # redémarre sans rebuild
# ou
docker compose down && docker compose up -d
```

---

## 8. Quand appeler Wael (à son retour)
- Un fichier d'une **structure nouvelle** que les 3 modes (table / matrix / multi_table) ne savent pas lire.
- Branchement de la **vraie base des codes SAGE** (remplacer le placeholder).
- Toute erreur qui persiste après les étapes du §7.

> Wael revient le **14 juillet**. En attendant, le système traite seul les fournisseurs connus ;
> seuls les **nouveaux** fournisseurs demandent une validation dans l'interface.
