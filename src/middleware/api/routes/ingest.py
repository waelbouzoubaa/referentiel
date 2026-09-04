from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from middleware.core.config import get_settings
from middleware.core.logging import get_logger
from middleware.storage.minio_client import upload_raw_file

logger = get_logger(__name__)
router = APIRouter()

PENDING_DIR = Path("/app/uploads/pending")
EXPORTS_DIR = Path("/app/exports")

# ── Email d'audit envoyé quand un fichier est auto-validé par reconnaissance de
# structure (aucune intervention humaine) — best-effort, jamais bloquant. Sert à
# ce qu'un humain puisse relire après coup et corriger si le match était erroné
# (ex: deux fournisseurs différents avec des en-têtes coïncidemment identiques).
# Modifiable directement ici : {supplier}, {matched_supplier}, {filename},
# {folder}, {pending_id}, {link} sont remplacés automatiquement à l'envoi.
AUTO_VALIDATION_EMAIL_SUBJECT = (
    "[Audit] Export automatique par reconnaissance de structure — {supplier}"
)

AUTO_VALIDATION_EMAIL_BODY = """Bonjour,

Un fichier a été exporté automatiquement vers Gery sans validation humaine —
sa structure de colonnes correspondait exactement à celle d'un fournisseur
déjà connu ({matched_supplier}).

Fournisseur (nouveau) : {supplier}
Fichier : {filename}
Dossier SharePoint : {folder}
ID de la demande : {pending_id}

{link}

Merci de vérifier que ce rapprochement est correct — si {supplier} n'est en
réalité pas apparenté à {matched_supplier}, corrigez le mapping dans
l'interface de validation.

Cordialement,
Middleware Ramery"""


class UnknownIngestRequest(BaseModel):
    filename: str
    folder_name: str
    file_path: str
    pending_id: str | None = None
    web_url: str | None = None
    sharepoint_item_id: str | None = None


class UnknownIngestResponse(BaseModel):
    pending_id: str
    supplier_guess: str
    message: str


def _inject_supplier_code(yaml_content: str, supplier_code: str) -> str:
    """Remplace supplier_code dans un YAML repris d'un autre fournisseur (structure match).

    Indispensable : sans ça, approuver la suggestion réécrirait le fichier YAML du
    fournisseur d'origine (même supplier_code → même chemin config/suppliers/{code}_v1.yaml).
    """
    import re
    return re.sub(
        r'^supplier_code:.*$',
        f'supplier_code: "{supplier_code}"',
        yaml_content,
        count=1,
        flags=re.MULTILINE,
    )


def _inject_sharepoint_folder(yaml_content: str, folder_name: str) -> str:
    """Corrige sharepoint_folder dans le YAML généré avec le vrai dossier SharePoint source."""
    import re
    folder_line = f'sharepoint_folder: "{folder_name}"'

    if re.search(r'^sharepoint_folder:', yaml_content, re.MULTILINE):
        yaml_content = re.sub(r'^sharepoint_folder:.*$', folder_line, yaml_content, flags=re.MULTILINE)
    else:
        yaml_content = re.sub(
            r'(^supplier_code:.*$)',
            r'\1\n' + folder_line,
            yaml_content,
            count=1,
            flags=re.MULTILINE,
        )

    if not re.search(r'^filename_keywords:', yaml_content, re.MULTILINE):
        yaml_content = re.sub(
            r'(^sharepoint_folder:.*$)',
            r'\1\nfilename_keywords: []',
            yaml_content,
            count=1,
            flags=re.MULTILINE,
        )

    return yaml_content


def _norm(text: str) -> str:
    """Normalise un texte Unicode (forme NFC) avant comparaison.

    SharePoint/Graph renvoie parfois les noms de fichiers en forme décomposée
    (ex: "é" = "e" + accent combinant séparé) plutôt que composée — visuellement
    identique mais PAS égal en comparaison stricte, ce qui faisait rater la
    détection de doublon pour tout fichier avec un caractère accentué.
    """
    return unicodedata.normalize("NFC", text or "")


def _find_pending_for_file(
    folder_name: str, filename: str, sharepoint_item_id: str | None = None
) -> dict | None:
    """Retourne une demande déjà ouverte (pending ou needs_support) pour ce fichier.

    Priorité à l'item ID SharePoint (clé stable), fallback sur (dossier, nom),
    comparés après normalisation Unicode (voir _norm). Les demandes déjà
    approved/rejected ne comptent pas — une nouvelle version du même fichier doit
    repartir sur une demande fraîche.
    """
    if not PENDING_DIR.exists():
        return None
    folder_name = _norm(folder_name)
    filename = _norm(filename)
    for meta_path in PENDING_DIR.glob("*.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("status") not in ("pending", "needs_support"):
            continue
        if sharepoint_item_id and data.get("sharepoint_item_id") == sharepoint_item_id:
            return data
        if (
            _norm(data.get("folder_name", "")) == folder_name
            and _norm(data.get("filename", "")) == filename
        ):
            return data
    return None


def _apply_pending_suggestion(
    pending_id: str,
    *,
    yaml_proposed: str,
    supplier_guess: str,
    confidence: int,
    confidence_source: str,
    initial_prompt: str = "",
) -> bool:
    """Écrit une suggestion (structure connue ou IA) dans le pending JSON.

    Retourne False sans rien écrire si la demande a disparu ou a déjà été
    traitée entre-temps (ne jamais écraser une action utilisateur concurrente).
    """
    meta_path = PENDING_DIR / f"{pending_id}.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if meta.get("status") != "pending":
        return False

    meta["yaml_proposed"] = yaml_proposed
    meta["initial_prompt"] = initial_prompt
    meta["confidence"] = confidence
    meta["confidence_source"] = confidence_source
    meta["supplier_guess"] = supplier_guess
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _rule_to_yaml_text(rule) -> str:
    """Sérialise une MappingRule (objet, pas texte brut édité) en YAML lisible.

    Utilisé uniquement pour la règle recalculée par l'auto-validation complète
    (build_auto_validated_rule) — partout ailleurs, le YAML reste le texte brut
    généré par l'IA ou édité à la main, jamais reconstruit depuis l'objet.
    """
    import io

    from ruamel.yaml import YAML

    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    stream = io.StringIO()
    yaml.dump(rule.model_dump(mode="json", exclude_none=True), stream)
    return stream.getvalue()


def _send_auto_validation_audit_email(
    pending_id: str, meta: dict, matched_supplier: str
) -> None:
    """Envoie l'email d'audit après une auto-validation par structure (best-effort)."""
    from middleware.notifications import send_support_notification

    review_ui_url = get_settings().review_ui_url
    link = (
        f"Ouvrir l'interface de validation : {review_ui_url}"
        if review_ui_url
        else "Interface de validation : (lien non configuré — voir MIDDLEWARE_REVIEW_UI_URL)"
    )
    values = {
        "supplier": meta.get("supplier_code") or meta.get("supplier_guess", "?"),
        "matched_supplier": matched_supplier,
        "filename": meta.get("filename", "?"),
        "folder": meta.get("folder_name", "?"),
        "pending_id": pending_id,
        "link": link,
    }
    send_support_notification(
        AUTO_VALIDATION_EMAIL_SUBJECT.format(**values),
        AUTO_VALIDATION_EMAIL_BODY.format(**values),
    )


async def _try_full_auto_validation(
    pending_id: str,
    file_path: Path,
    folder_name: str,
    filename: str,
    sharepoint_item_id: str | None,
    matched_code: str,
    matched_row: int,
    supplier_guess: str,
) -> bool:
    """Tente l'auto-validation complète (0 clic humain) d'un fichier à structure connue.

    N'auto-valide QUE si toutes les garanties tiennent : plage de données
    recalculée avec succès (voir build_auto_validated_rule), cartouche
    (SIREN/dates/code générique) qui s'extrait là où il s'extrayait sur le
    fichier d'origine (verify_file_metadata_presence — jamais en comparant les
    valeurs), et aucun souci de cohérence à l'extraction. Au moindre doute,
    retourne False sans rien avoir modifié — l'appelant retombe alors sur la
    suggestion pré-remplie + validation humaine, comme avant.
    """
    from middleware.api.routes.processing import _check_coherence
    from middleware.db.session import AsyncSessionLocal
    from middleware.pipeline import parse_with_rule, process_and_export
    from middleware.structure_index import (
        build_auto_validated_rule,
        update_fingerprint,
        verify_file_metadata_presence,
    )

    rule = build_auto_validated_rule(matched_code, file_path, matched_row)
    if rule is None:
        return False
    rule = rule.model_copy(update={
        "supplier_code": supplier_guess,
        "sharepoint_folder": folder_name,
        "filename_keywords": [],
    })

    try:
        result = parse_with_rule(file_path, rule)
    except Exception as exc:
        logger.warning(
            "parsing avec règle recalculée échoué — repli validation humaine",
            pending_id=pending_id, erreur=str(exc),
        )
        return False

    if _check_coherence(result, rule):
        return False
    if not verify_file_metadata_presence(matched_code, result.file_metadata):
        logger.info(
            "cartouche incomplet par rapport au fournisseur matché — repli validation humaine",
            pending_id=pending_id, matched_supplier=matched_code,
        )
        return False

    try:
        async with AsyncSessionLocal() as session:
            try:
                parse_result, _, export_result = await process_and_export(
                    session, rule, file_path, EXPORTS_DIR,
                    original_filename=filename, sharepoint_item_id=sharepoint_item_id,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception as exc:
        logger.warning(
            "export automatique par structure échoué — repli validation humaine",
            pending_id=pending_id, erreur=str(exc),
        )
        return False

    yaml_text = _rule_to_yaml_text(rule)

    CONFIG_DIR = Path("config/suppliers")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / f"{supplier_guess}_v1.yaml").write_text(yaml_text, encoding="utf-8")
    update_fingerprint(supplier_guess, file_path, rule, parse_result.file_metadata)

    meta_path = PENDING_DIR / f"{pending_id}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    meta.update({
        "yaml_proposed": yaml_text,
        "confidence": 95,
        "confidence_source": "structure_match_auto",
        "supplier_guess": supplier_guess,
        "supplier_code": supplier_guess,
        "status": "approved",
        "exports": [f.path.name for f in export_result.files],
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "auto-validation complète — structure connue, cartouche cohérent, 0 clic humain",
        pending_id=pending_id, matched_supplier=matched_code, supplier_guess=supplier_guess,
    )
    _send_auto_validation_audit_email(pending_id, meta, matched_code)
    return True


async def _generate_ai_suggestion_background(
    pending_id: str,
    file_path: Path,
    folder_name: str,
    filename: str,
    sharepoint_item_id: str | None = None,
) -> None:
    """Tâche de fond : propose un mapping pour ce fichier et met à jour le pending JSON.

    Tourne APRÈS que la réponse HTTP soit déjà repartie vers le watcher — un appel
    Gemini éventuel (jusqu'à 1-2 min) ne bloque donc jamais le reste de l'API
    (interface, autres requêtes). Best-effort : si tout échoue, la demande reste
    avec un YAML vide (le bouton « Générer avec l'IA » de l'interface reste
    disponible).

    Essaie d'abord une correspondance de structure EXACTE avec un fournisseur déjà
    validé (mêmes libellés de colonnes, à la même position — voir structure_index.py).
    Si trouvée : tente l'auto-validation complète (voir _try_full_auto_validation) ;
    si les garanties supplémentaires ne tiennent pas, reprend simplement son YAML
    comme suggestion pré-remplie (rapide, gratuit, déterministe, mais validation
    humaine requise). Si aucune structure ne correspond : génération IA fraîche,
    comportement inchangé.
    """
    from middleware.structure_index import find_matching_supplier

    try:
        match = find_matching_supplier(file_path)
    except Exception as exc:
        logger.warning(
            "recherche de structure connue échouée (ignorée)",
            pending_id=pending_id, erreur=str(exc),
        )
        match = None

    if match is not None:
        matched_code, matched_row = match
        yaml_path = Path("config/suppliers") / f"{matched_code}_v1.yaml"
        if yaml_path.exists():
            supplier_guess = folder_name.lower().replace(" ", "_").replace("-", "_")

            try:
                auto_validated = await _try_full_auto_validation(
                    pending_id, file_path, folder_name, filename,
                    sharepoint_item_id, matched_code, matched_row, supplier_guess,
                )
            except Exception as exc:
                logger.warning(
                    "auto-validation complète échouée (ignorée) — repli suggestion",
                    pending_id=pending_id, erreur=str(exc),
                )
                auto_validated = False
            if auto_validated:
                return

            yaml_proposed = yaml_path.read_text(encoding="utf-8")
            yaml_proposed = _inject_supplier_code(yaml_proposed, supplier_guess)
            yaml_proposed = _inject_sharepoint_folder(yaml_proposed, folder_name)
            ok = _apply_pending_suggestion(
                pending_id,
                yaml_proposed=yaml_proposed,
                supplier_guess=supplier_guess,
                confidence=95,
                confidence_source="structure_match",
            )
            if ok:
                logger.info(
                    "structure déjà connue — YAML repris sans appel IA",
                    pending_id=pending_id, matched_supplier=matched_code,
                    supplier_guess=supplier_guess,
                )
            return

    try:
        from middleware.ai.yaml_generator import generate_yaml_from_excel

        supplier_guess, yaml_content, prompt, confidence = generate_yaml_from_excel(
            file_path=file_path, folder_name=folder_name, filename=filename,
        )
        yaml_proposed = _inject_sharepoint_folder(yaml_content, folder_name)
    except Exception as exc:
        logger.warning(
            "génération IA automatique échouée (tâche de fond) — demande reste sans suggestion",
            pending_id=pending_id,
            filename=filename,
            erreur=str(exc),
        )
        return

    ok = _apply_pending_suggestion(
        pending_id,
        yaml_proposed=yaml_proposed,
        supplier_guess=supplier_guess,
        confidence=confidence,
        confidence_source="ai_generated",
        initial_prompt=prompt,
    )
    if ok:
        logger.info(
            "suggestion IA (tâche de fond) prête", pending_id=pending_id, confidence=confidence
        )


@router.post("/ingest/unknown", response_model=UnknownIngestResponse, tags=["ingestion"])
async def ingest_unknown(
    request: UnknownIngestRequest, background_tasks: BackgroundTasks
) -> UnknownIngestResponse:
    """Reçoit un fichier inconnu, l'archive dans MinIO et crée une demande de
    validation — la suggestion IA (YAML + confiance) est générée en tâche de fond
    juste après, sans bloquer la réponse ni le reste de l'API.
    """
    file_path = Path(request.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    existing = _find_pending_for_file(request.folder_name, request.filename, request.sharepoint_item_id)
    if existing is not None:
        file_path.unlink(missing_ok=True)
        logger.info(
            "doublon évité — demande déjà en attente",
            pending_id=existing["id"],
            filename=request.filename,
        )
        return UnknownIngestResponse(
            pending_id=existing["id"],
            supplier_guess=existing.get("supplier_guess", ""),
            message="Une demande est déjà en attente de validation pour ce fichier.",
        )

    pending_id = request.pending_id or uuid.uuid4().hex
    supplier_guess = request.folder_name.lower().replace(" ", "_").replace("-", "_")

    # Archive le fichier brut dans MinIO dès la détection
    content_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()[:12]
    minio_path = await upload_raw_file(file_path, supplier_guess, content_hash)

    meta = {
        "id": pending_id,
        "created_at": datetime.utcnow().isoformat(),
        "filename": request.filename,
        "folder_name": request.folder_name,
        "file_path": request.file_path,
        "supplier_guess": supplier_guess,
        "yaml_proposed": "",
        "initial_prompt": "",
        "confidence": None,
        "confidence_source": None,
        "web_url": request.web_url,
        "sharepoint_item_id": request.sharepoint_item_id,
        "minio_path": minio_path,
        "status": "pending",
    }
    (PENDING_DIR / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "nouveau fichier en attente de validation",
        pending_id=pending_id,
        filename=request.filename,
        folder=request.folder_name,
        minio_path=minio_path,
    )

    # Suggestion IA en tâche de fond — ne bloque ni cette réponse ni le reste de l'API
    background_tasks.add_task(
        _generate_ai_suggestion_background,
        pending_id, file_path, request.folder_name, request.filename,
        request.sharepoint_item_id,
    )

    return UnknownIngestResponse(
        pending_id=pending_id,
        supplier_guess=supplier_guess,
        message=f"Fichier '{request.filename}' en attente de validation.",
    )
