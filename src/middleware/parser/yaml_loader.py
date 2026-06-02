from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from middleware.core.exceptions import MappingValidationError
from middleware.core.logging import get_logger
from middleware.parser.grammar import MappingRule

logger = get_logger(__name__)


def load_mapping_rule(path: Path) -> MappingRule:
    """Charge et valide un fichier YAML de mapping fournisseur.

    Args:
        path: Chemin vers le fichier YAML.

    Returns:
        MappingRule validé.

    Raises:
        MappingValidationError: Si le fichier est introuvable, invalide ou
            échoue la validation Pydantic.
    """
    try:
        from ruamel.yaml import YAML  # import tardif pour éviter l'erreur si non installé
    except ImportError as exc:
        raise MappingValidationError(
            "ruamel.yaml non installé. Lancez : pip install ruamel.yaml"
        ) from exc

    if not path.exists():
        raise MappingValidationError(
            f"Fichier YAML introuvable : {path}",
        )

    yaml = YAML(typ="safe")
    try:
        raw = yaml.load(path)
    except Exception as exc:
        raise MappingValidationError(
            f"Erreur de lecture YAML ({path.name}) : {exc}",
        ) from exc

    if not isinstance(raw, dict):
        raise MappingValidationError(
            f"Le fichier YAML doit être un dictionnaire, reçu : {type(raw).__name__}",
        )

    try:
        rule = MappingRule.model_validate(raw)
    except ValidationError as exc:
        erreurs = "; ".join(
            f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        raise MappingValidationError(
            f"Validation Pydantic échouée pour {path.name} : {erreurs}",
            supplier_code=raw.get("supplier_code"),
        ) from exc

    logger.info(
        "mapping YAML chargé",
        supplier_code=rule.supplier_code,
        version=rule.mapping_version,
        mode=rule.extraction_mode,
    )
    return rule


def load_all_mappings(folder: Path) -> dict[str, MappingRule]:
    """Charge tous les fichiers YAML d'un dossier, retourne un dict code→règle.

    Seule la version active (version la plus haute) est retournée par fournisseur.
    """
    rules: dict[str, MappingRule] = {}

    for yaml_file in sorted(folder.glob("*.yaml")):
        try:
            rule = load_mapping_rule(yaml_file)
        except MappingValidationError as exc:
            logger.warning("mapping ignoré (erreur)", fichier=yaml_file.name, erreur=str(exc))
            continue

        code = rule.supplier_code
        if code not in rules or rule.mapping_version > rules[code].mapping_version:
            rules[code] = rule

    logger.info("mappings chargés", count=len(rules), codes=list(rules.keys()))
    return rules
