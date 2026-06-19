"""Résolution du Code Fournisseur SAGE — point d'intégration unique et remplaçable.

Le reste du code n'appelle QUE `resolve_sage_code(...)` : il ne sait pas d'où vient
la donnée. Aujourd'hui, backend "file" (placeholder : un CSV de correspondance).
Demain, on branche une vraie source (cloud / API / base) en ajoutant un backend ici
et en changeant la config — sans rien modifier dans l'export ni le pipeline.

Format du CSV placeholder (chemin = MIDDLEWARE_SAGE_MAPPING_FILE) :
    code_fournisseur,code_sage
    airisol,FR0001234
"""
from __future__ import annotations

import csv
from pathlib import Path

from middleware.core.config import get_settings
from middleware.core.logging import get_logger

logger = get_logger(__name__)


def resolve_sage_code(code_fournisseur: str) -> str | None:
    """Retourne le Code Fournisseur SAGE pour un fournisseur, ou None si inconnu.

    Ne lève jamais : une source absente/indisponible → None (colonne vide), pas de crash.
    """
    if not code_fournisseur:
        return None
    settings = get_settings()
    backend = settings.sage_backend
    if backend == "file":
        return _resolve_from_file(code_fournisseur, settings.sage_mapping_file)
    # Futurs backends (cloud/base) : les brancher ici, même signature.
    logger.warning("backend SAGE non géré", backend=backend)
    return None


def _resolve_from_file(code_fournisseur: str, path_str: str) -> str | None:
    path = Path(path_str)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("code_fournisseur") or "").strip() == code_fournisseur:
                    return (row.get("code_sage") or "").strip() or None
    except Exception as exc:
        logger.warning("lecture du mapping SAGE échouée", path=path_str, erreur=str(exc))
    return None
