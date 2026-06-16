#!/usr/bin/env bash
#
# Réinitialise l'environnement de test (DESTRUCTIF).
#
# Vide :
#   - toutes les tables applicatives PostgreSQL (schéma public ; alembic_version conservé,
#     schéma n8n non touché)
#   - les fichiers en attente de validation (/app/uploads) et les exports (/app/exports)
#   - le stockage objet MinIO (fichiers bruts archivés)
#   - les YAML fournisseurs générés par l'IA (non versionnés dans git)
#
# Conserve :
#   - les YAML fournisseurs versionnés (atlantic_scga_*, airisol, agenor)
#   - le schéma de base et l'historique des migrations
#
# Le watcher est arrêté pendant l'opération puis recréé : au prochain cycle il
# re-scanne tout SharePoint depuis zéro (re-détection complète des fichiers).
#
# Usage (depuis la racine du repo, sur le VPS) :
#   bash scripts/reset_test_env.sh --yes

set -euo pipefail

if [ "${1:-}" != "--yes" ]; then
  echo "⚠️  Opération DESTRUCTIVE (base, fichiers, exports, MinIO)."
  echo "    Relance avec --yes pour confirmer :"
  echo "      bash scripts/reset_test_env.sh --yes"
  exit 1
fi

echo ">>> 1/6 Arrêt du watcher (évite tout re-traitement pendant le reset)..."
docker compose stop watcher

echo ">>> 2/6 Truncate des tables applicatives (alembic_version conservé)..."
docker compose exec -T postgres psql -U middleware -d middleware -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables
           WHERE schemaname = 'public' AND tablename <> 'alembic_version'
  LOOP
    EXECUTE 'TRUNCATE TABLE public.' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE';
  END LOOP;
END $$;
SQL

echo ">>> 3/6 Purge des fichiers pending et des exports..."
docker compose exec -T api sh -c 'find /app/uploads -type f -delete 2>/dev/null; find /app/exports -type f -delete 2>/dev/null; true'

echo ">>> 4/6 Purge du stockage MinIO (optionnel, non bloquant)..."
docker compose run --rm --entrypoint sh minio-init -c \
  'mc alias set local http://minio:9000 minioadmin minioadmin && \
   mc rm --recursive --force local/middleware-dev || true; \
   mc mb --ignore-existing local/middleware-dev' \
  || echo "    (purge MinIO ignorée — sans impact sur les re-tests)"

echo ">>> 5/6 Suppression des YAML générés par l'IA (non versionnés)..."
git clean -f config/suppliers/

echo ">>> 6/6 Recréation du watcher (re-scan complet au prochain cycle)..."
docker compose up -d --force-recreate watcher

echo ""
echo "✅ Reset terminé. Base vide, fichiers/exports purgés, YAML IA supprimés."
echo "   Le watcher va re-détecter les fichiers SharePoint au prochain polling."
