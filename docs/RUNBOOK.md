# Runbook opérationnel — Middleware catalogues fournisseurs → Gery

Guide pour **faire tourner et dépanner** le middleware **sans le développeur** (Wael).
Tout se passe sur le VPS. Les commandes sont à copier-coller.

---

## 0. L'essentiel en 30 secondes
- **À quoi ça sert** : lire les catalogues Excel fournisseurs (SharePoint), les normaliser, et produire des fichiers CSV d'import pour **Gery**.
- **Interface** : `https://refs-fournisseurs.devtools.maikhub.com/` (validation des fournisseurs + exports).
- **Aucun fichier ne s'exporte tout seul** (changement volontaire depuis fin juillet 2026) :
  même un fournisseur déjà connu passe par une demande de validation à chaque fichier — la
  structure d'un même fournisseur peut varier d'un fichier à l'autre, donc pas d'auto-
  reconnaissance fiable. Le métier valide (ou corrige) via un formulaire simplifié
  pré-rempli par l'IA ; s'il n'y arrive pas, il clique **« 🆘 Demander l'aide du support »**
  pour que l'équipe dev traite le cas.
- **Rien ne se perd** : base PostgreSQL + fichiers bruts (MinIO) + exports (disque), tous persistés.

---

## 1. Se connecter au serveur
```bash
ssh wael@72.60.189.114
cd ~/middleware-ramery
```

| Service | Adresse |
|---|---|
| Interface de validation (Streamlit) | https://refs-fournisseurs.devtools.maikhub.com/ (accès restreint par IP, voir `docker-compose.yml` → label `ip-allowlist`) |
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

**Si tu dois pousser un commit fait directement sur le VPS** (`git push`) et que
l'authentification par mot de passe GitHub échoue (`Password authentication is not
supported`) : GitHub exige un **Personal Access Token** à la place du mot de passe.
Le générer sur https://github.com/settings/tokens (classique, coche la case `repo`
entière — sinon `403 Permission denied` même avec un token valide), puis le coller
comme mot de passe quand `git push` le demande. Pour ne pas le retaper à chaque fois :
`git config --global credential.helper store`.

---

## 4. Voir les logs (pour comprendre ce qui se passe)
```bash
docker compose logs --tail=50 watcher     # détection des fichiers SharePoint
docker compose logs --tail=50 api         # traitement / erreurs
docker compose logs --tail=50 review_ui   # interface
```
Dans les logs du watcher, on voit : `[AJOUTE]/[MODIFIE] <fichier> → ...`. Deux cas :
- **Fournisseur avec un YAML committé** (rare désormais, voir §0) : `Envoyé pour
  validation métier (ID: ...)` — le YAML connu est proposé comme suggestion, mais la
  demande passe quand même par l'interface, aucun export automatique.
- **Fournisseur/fichier inconnu** : `Envoyé pour validation. Fournisseur suggéré : '...'`
  — apparaît dans l'interface sans YAML pré-rempli (il faut cliquer « Générer avec l'IA »
  ou en écrire un).
- `Erreur HTTP 4xx/5xx` = problème technique (fichier illisible, etc.) → voir §7.

---

## 5. Opérations courantes

### a) Valider un fichier (le cas de tous les jours)
1. Toute nouvelle demande apparaît dans l'interface, onglet latéral **« En attente »**.
2. Ouvrir la demande : à droite l'aperçu Excel (grille A,B,C) + l'aperçu export Gery ; à
   gauche l'édition (onglets YAML / **🧩 Formulaire simplifié** / Formulaire avancé /
   Assistant IA).
3. Si l'onglet YAML est vide : cliquer **« 🤖 Générer avec l'IA »** (appel Gemini à
   chaque clic, ~30s à 2min — une jauge de confiance % s'affiche ensuite).
4. Aller sur **🧩 Formulaire simplifié** : chaque colonne de l'export Gery est une
   section repliable avec une liste déroulante sur les colonnes réellement détectées
   dans le fichier — pas besoin de connaître le YAML. Un badge indique si le cas est
   🟢 Simple ou 🟠 Compliqué (mode matrix/multi_table, ou confiance IA < 70%).
5. Vérifier avec **« Aperçu export Gery »** que le nombre de lignes/produits semble correct.
6. Cliquer **✅ Valider et générer les exports** → c'est SEULEMENT à ce moment que
   l'export Gery est réellement généré (DB + CSV). Rien avant.

### b) Cas trop compliqué pour le métier
Bouton **« 🆘 Demander l'aide du support »** (à la place de Valider) → la demande passe
dans la file latérale **« 🆘 Aide support »**. L'équipe dev la traite depuis là (YAML à la
main, Formulaire avancé, ou Assistant IA), puis clique Valider normalement. Bouton inverse
**« ↩️ Remettre en attente métier »** si besoin de la renvoyer.

> Détail pour créer un mapping à la main : `docs/GUIDE_YAML_TABLE.md` (mode table),
> `GUIDE_YAML_MATRIX.md` (grille de prix), `GUIDE_YAML_MULTI_TABLE.md` (plusieurs tableaux).

### c) Récupérer les fichiers Gery générés
Interface → barre latérale → **Vue : « Exports Gery »** → liste des CSV + bouton de téléchargement.
(ou sur le serveur : `docker compose exec api ls -lah /app/exports`)

### d) Renseigner les codes fournisseurs SAGE (placeholder)
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

**« Un export Gery n'est jamais généré »**
- Normal tant que la demande n'a pas été **validée manuellement** dans l'interface (§0) —
  plus aucun export automatique. Vérifier l'onglet « En attente » ou « 🆘 Aide support ».
- `Erreur HTTP 500/422` dans les logs de l'API = souci de mapping. Cause la plus fréquente :
  **nom d'onglet**. Corriger le YAML (onglet YAML de la demande, ou
  `config/suppliers/<code>_v1.yaml` si déjà validé) : mettre `sheet_match: "auto"`.

**« Une demande en attente affiche un YAML vide »**
- **Normal** pour un fichier vraiment inconnu — cliquer **« 🤖 Générer avec l'IA »** dans
  l'onglet YAML (le Formulaire simplifié affiche un message tant qu'il n'y a rien à éditer).
- Si un YAML apparaît tout seul avec tout mappé sur la colonne A sans avoir rien cliqué :
  signaler à Wael, ça correspond à un bug déjà rencontré (corrigé le 23/07/2026) — vérifier
  que le déploiement est à jour (`git log --oneline -1` doit inclure `790aa47` ou plus récent).

**« Générer avec l'IA » reste bloqué / timeout**
- L'appel à Gemini peut prendre jusqu'à 2 minutes sur un gros fichier — c'est normal, il y a
  une jauge de chargement. Si ça échoue quand même, recliquer (chaque clic est un appel
  frais, rien n'est perdu).

**« Repartir d'un environnement propre pour une démo/test »**
```bash
bash scripts/reset_test_env.sh --yes      # ⚠️ DESTRUCTIF : vide base, exports, pending, MinIO
```

**Revenir à la version précédente (rollback) si une mise à jour casse**
```bash
git status                                 # ⚠️ vérifier d'abord qu'il n'y a rien
                                            # de modifié localement (non commité) qui
                                            # serait perdu — un rollback a déjà cassé
                                            # Traefik le 21/07/2026 pour cette raison
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
