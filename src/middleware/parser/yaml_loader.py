from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from middleware.core.exceptions import MappingValidationError
from middleware.core.logging import get_logger
from middleware.parser.grammar import MappingRule

logger = get_logger(__name__)


def validate_mapping_yaml(yaml_text: str) -> tuple[MappingRule | None, list[str]]:
    """Valide un contenu YAML de mapping fournisseur (chaîne).

    Args:
        yaml_text: Contenu YAML brut.

    Returns:
        (MappingRule, []) si valide, ou (None, [messages d'erreur]) sinon.
    """
    try:
        from ruamel.yaml import YAML  # import tardif pour éviter l'erreur si non installé
    except ImportError:
        return None, ["ruamel.yaml non installé. Lancez : pip install ruamel.yaml"]

    yaml = YAML(typ="safe")
    try:
        raw = yaml.load(yaml_text)
    except Exception as exc:
        return None, [f"Erreur de lecture YAML : {exc}"]

    if not isinstance(raw, dict):
        return None, [f"Le YAML doit être un dictionnaire, reçu : {type(raw).__name__}"]

    try:
        rule = MappingRule.model_validate(raw)
    except ValidationError as exc:
        erreurs = [
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        ]
        return None, erreurs

    return rule, []


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
    if not path.exists():
        raise MappingValidationError(
            f"Fichier YAML introuvable : {path}",
        )

    rule, erreurs = validate_mapping_yaml(path.read_text(encoding="utf-8"))
    if erreurs:
        raise MappingValidationError(
            f"Validation échouée pour {path.name} : {'; '.join(erreurs)}",
        )
    assert rule is not None

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
