"""Interface de validation des mappings YAML générés par IA pour les fournisseurs inconnus.

Permet de relire l'aperçu du fichier Excel source, d'éditer le YAML proposé
(directement ou via un formulaire simplifié pour le mode 'table'), puis de
valider (génère les exports Gery) ou de rejeter la proposition.
"""
from __future__ import annotations

import io
import os
from typing import Any

import httpx
import streamlit as st
from ruamel.yaml import YAML

API_URL = os.environ.get("MIDDLEWARE_API_URL", "http://api:8000")

TRANSFORMS_VALIDES = [
    "strip",
    "strip_upper",
    "strip_lower",
    "to_uppercase",
    "to_lowercase",
    "parse_decimal_fr",
    "parse_decimal_us",
    "parse_date_fr",
    "parse_date_iso",
    "parse_duration_fr",
]

st.set_page_config(page_title="Validation mappings fournisseurs", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Lexend:wght@400;600;800&display=swap');
    h1, h2, h3, h4, .stTabs [data-baseweb="tab"] p {
        font-family: 'Lexend', sans-serif !important;
        color: #003D7C;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Helpers API ────────────────────────────────────────────────────────────

def api_get(path: str) -> httpx.Response:
    return httpx.get(f"{API_URL}{path}", timeout=30)


def api_put(path: str, json_body: dict[str, Any]) -> httpx.Response:
    return httpx.put(f"{API_URL}{path}", json=json_body, timeout=30)


def fetch_pending_list() -> list[dict[str, Any]]:
    resp = api_get("/api/v1/review/pending")
    resp.raise_for_status()
    return resp.json()


def fetch_detail(pending_id: str) -> dict[str, Any]:
    resp = api_get(f"/api/v1/review/{pending_id}")
    resp.raise_for_status()
    return resp.json()


def fetch_preview(pending_id: str) -> str:
    resp = api_get(f"/api/v1/review/{pending_id}/preview")
    if resp.status_code != 200:
        return f"(Aperçu indisponible : {resp.text})"
    return resp.json().get("preview", "")


# ─── Helpers YAML ───────────────────────────────────────────────────────────

def load_yaml(text: str) -> dict[str, Any]:
    yaml = YAML(typ="safe")
    data = yaml.load(text)
    return data if isinstance(data, dict) else {}


def dump_yaml(data: dict[str, Any]) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    buf = io.StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


def transform_to_str(transform: Any) -> str:
    if isinstance(transform, list):
        return ", ".join(transform)
    return transform or ""


def transform_from_str(text: str) -> str | list[str] | None:
    parts = [t.strip() for t in text.split(",") if t.strip()]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else parts


def coerce_value(text: str) -> Any:
    text = text.strip()
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def show_action_result(html: str) -> None:
    import streamlit.components.v1 as components

    components.html(html, height=180, scrolling=False)


# ─── Formulaire simplifié (mode table) ─────────────────────────────────────

def render_table_form(data: dict[str, Any], pending_id: str) -> dict[str, Any] | None:
    """Affiche le formulaire simplifié pour un mapping en mode 'table'.

    Returns:
        Le dict de mapping reconstruit si l'utilisateur a cliqué "Enregistrer", sinon None.
    """
    st.markdown("##### Informations générales")
    c1, c2 = st.columns(2)
    supplier_code = c1.text_input("Code fournisseur", value=data.get("supplier_code", ""))
    description = c2.text_input("Description", value=data.get("description", ""))

    c3, c4 = st.columns(2)
    upload_modes = ["full", "incremental"]
    upload_mode = data.get("upload_mode", "incremental")
    c3.selectbox(
        "Mode d'upload",
        upload_modes,
        index=upload_modes.index(upload_mode) if upload_mode in upload_modes else 1,
        key=f"upload_mode_{pending_id}",
    )

    sheet_match = data.get("sheet_match", "auto")
    if isinstance(sheet_match, dict):
        c4.info("sheet_match avancé (par mode) — modifiable uniquement via l'onglet YAML.")
    else:
        c4.text_input(
            "Nom de l'onglet (sheet_match)",
            value=str(sheet_match),
            key=f"sheet_match_{pending_id}",
        )

    header_detection = data.get("header_detection") or {}
    c5, c6 = st.columns(2)
    header_row = c5.number_input(
        "Ligne d'en-tête", min_value=1, step=1,
        value=int(header_detection.get("row") or 1),
        key=f"header_row_{pending_id}",
    )
    data_starts_row = c6.number_input(
        "Première ligne de données", min_value=1, step=1,
        value=int(data.get("data_starts_row") or (header_row + 1)),
        key=f"data_starts_row_{pending_id}",
    )

    row_filter = data.get("row_filter") or {}
    must_have_str = st.text_input(
        "Colonnes obligatoires non vides (séparées par des virgules)",
        value=", ".join(row_filter.get("must_have_value_in", [])),
        key=f"must_have_{pending_id}",
        help="Ex: B  → les lignes sans valeur en colonne B sont ignorées.",
    )
    exclude_starts_str = st.text_input(
        "Exclure les lignes dont la colonne commence par (séparées par virgules)",
        value=", ".join(row_filter.get("exclude_if_starts_with", [])),
        key=f"exclude_starts_{pending_id}",
    )

    st.markdown("##### Colonnes")
    st.caption(
        "Une ligne par champ du produit. Renseignez soit `source_col` (lettre de colonne), "
        "soit `constant` (valeur fixe). `transform` : liste de transformations séparées "
        "par virgules."
    )
    columns_dict = data.get("columns") or {}
    columns_rows = []
    for field_name, col in columns_dict.items():
        col = col or {}
        columns_rows.append({
            "champ": field_name,
            "source_col": col.get("source_col", "") or "",
            "constant": col.get("constant", "") or "",
            "transform": transform_to_str(col.get("transform")),
            "required": bool(col.get("required", False)),
        })
    columns_df = st.data_editor(
        columns_rows,
        num_rows="dynamic",
        use_container_width=True,
        key=f"columns_editor_{pending_id}",
        column_config={
            "champ": st.column_config.TextColumn(
                "Champ", help="ex: supplier_product_code, designation, family"
            ),
            "source_col": st.column_config.TextColumn("Colonne source (ex: B)"),
            "constant": st.column_config.TextColumn("Valeur fixe"),
            "transform": st.column_config.TextColumn("Transformations (virgules)"),
            "required": st.column_config.CheckboxColumn("Obligatoire"),
        },
    )
    st.caption("Transformations valides : " + ", ".join(TRANSFORMS_VALIDES))

    st.markdown("##### Prix")
    prices_rows = []
    for price in data.get("prices") or []:
        price = price or {}
        prices_rows.append({
            "type": price.get("type", ""),
            "source_col": price.get("source_col", ""),
            "transform": transform_to_str(price.get("transform", "parse_decimal_fr")),
            "currency": price.get("currency", "EUR"),
        })
    prices_df = st.data_editor(
        prices_rows,
        num_rows="dynamic",
        use_container_width=True,
        key=f"prices_editor_{pending_id}",
        column_config={
            "type": st.column_config.TextColumn("Type (ex: public, installer)"),
            "source_col": st.column_config.TextColumn("Colonne source"),
            "transform": st.column_config.TextColumn("Transformation"),
            "currency": st.column_config.TextColumn("Devise"),
        },
    )

    st.markdown("##### Attributs techniques")
    attributes_rows = []
    for attr in data.get("attributes") or []:
        attr = attr or {}
        attributes_rows.append({
            "key": attr.get("key", ""),
            "source_col": attr.get("source_col", ""),
            "data_type": attr.get("data_type", "string"),
            "unit": attr.get("unit", "") or "",
            "transform": transform_to_str(attr.get("transform")),
        })
    attributes_df = st.data_editor(
        attributes_rows,
        num_rows="dynamic",
        use_container_width=True,
        key=f"attributes_editor_{pending_id}",
        column_config={
            "key": st.column_config.TextColumn("Clé attribut"),
            "source_col": st.column_config.TextColumn("Colonne source"),
            "data_type": st.column_config.TextColumn("Type (string/integer/decimal/enum)"),
            "unit": st.column_config.TextColumn("Unité"),
            "transform": st.column_config.TextColumn("Transformation"),
        },
    )

    st.markdown("##### Validité du tarif")
    file_metadata = data.get("file_metadata") or {}
    vs = file_metadata.get("validity_start") or {}
    ve = file_metadata.get("validity_end") or {}
    c7, c8 = st.columns(2)
    vs_cell = c7.text_input(
        "Cellule date de début de validité", value=vs.get("cell", "") or "",
        key=f"vs_cell_{pending_id}",
    )
    ve_cell = c8.text_input(
        "Cellule date de fin de validité", value=ve.get("cell", "") or "",
        key=f"ve_cell_{pending_id}",
    )

    st.markdown("##### Export Gery")
    gery_export = data.get("gery_export") or {}
    enabled = st.checkbox(
        "Export Gery activé",
        value=gery_export.get("enabled", True),
        key=f"ge_enabled_{pending_id}",
    )
    blocked_reason = ""
    if not enabled:
        blocked_reason = st.text_input(
            "Raison du blocage (obligatoire si export désactivé)",
            value=gery_export.get("blocked_reason", "") or "",
            key=f"ge_blocked_reason_{pending_id}",
        )
    strategies = ["cartesian", "best_price_only", "skip_for_review"]
    flatten_strategy = gery_export.get("flatten_strategy", "cartesian")
    st.selectbox(
        "Stratégie de mise à plat",
        strategies,
        index=strategies.index(flatten_strategy) if flatten_strategy in strategies else 0,
        key=f"ge_flatten_{pending_id}",
    )

    defaults_rows = [
        {"clé": k, "valeur": str(v)} for k, v in (gery_export.get("defaults") or {}).items()
    ]
    st.caption("Valeurs par défaut Gery (clé / valeur)")
    defaults_df = st.data_editor(
        defaults_rows,
        num_rows="dynamic",
        use_container_width=True,
        key=f"defaults_editor_{pending_id}",
        column_config={
            "clé": st.column_config.TextColumn("Clé"),
            "valeur": st.column_config.TextColumn("Valeur"),
        },
    )

    price_export_mapping = gery_export.get("price_export_mapping") or {}
    direct_unit_cost = st.text_input(
        "Type de prix utilisé pour 'Direct Unit Cost'",
        value=price_export_mapping.get("direct_unit_cost", "installer"),
        key=f"ge_direct_unit_cost_{pending_id}",
    )

    if not st.button("Enregistrer (formulaire)", key=f"save_form_{pending_id}"):
        return None

    # ── Reconstruction du dict de mapping ───────────────────────────────────
    new_columns: dict[str, Any] = {}
    for row in columns_df:
        champ = (row.get("champ") or "").strip()
        if not champ:
            continue
        entry: dict[str, Any] = {}
        source_col = (row.get("source_col") or "").strip()
        constant = (row.get("constant") or "").strip()
        if source_col:
            entry["source_col"] = source_col
        elif constant:
            entry["constant"] = constant
        transform = transform_from_str(row.get("transform") or "")
        if transform:
            entry["transform"] = transform
        if row.get("required"):
            entry["required"] = True
        new_columns[champ] = entry

    new_prices = []
    for row in prices_df:
        price_type = (row.get("type") or "").strip()
        source_col = (row.get("source_col") or "").strip()
        if not price_type or not source_col:
            continue
        entry = {
            "type": price_type,
            "source_col": source_col,
            "transform": transform_from_str(row.get("transform") or "") or "parse_decimal_fr",
            "currency": (row.get("currency") or "EUR").strip() or "EUR",
        }
        new_prices.append(entry)

    new_attributes = []
    for row in attributes_df:
        key = (row.get("key") or "").strip()
        source_col = (row.get("source_col") or "").strip()
        if not key or not source_col:
            continue
        entry = {
            "key": key,
            "source_col": source_col,
            "data_type": (row.get("data_type") or "string").strip() or "string",
        }
        unit = (row.get("unit") or "").strip()
        if unit:
            entry["unit"] = unit
        transform = transform_from_str(row.get("transform") or "")
        if transform:
            entry["transform"] = transform
        new_attributes.append(entry)

    new_row_filter: dict[str, Any] = {}
    must_have = [c.strip() for c in must_have_str.split(",") if c.strip()]
    if must_have:
        new_row_filter["must_have_value_in"] = must_have
    exclude_starts = [c.strip() for c in exclude_starts_str.split(",") if c.strip()]
    if exclude_starts:
        new_row_filter["exclude_if_starts_with"] = exclude_starts

    new_file_metadata = dict(file_metadata)
    if vs_cell.strip():
        new_file_metadata["validity_start"] = {
            **vs, "cell": vs_cell.strip(), "transform": vs.get("transform", "parse_date_iso"),
        }
    else:
        new_file_metadata.pop("validity_start", None)
    if ve_cell.strip():
        new_file_metadata["validity_end"] = {
            **ve, "cell": ve_cell.strip(), "transform": ve.get("transform", "parse_date_iso"),
        }
    else:
        new_file_metadata.pop("validity_end", None)

    new_defaults: dict[str, Any] = {}
    for row in defaults_df:
        key = (row.get("clé") or "").strip()
        if not key:
            continue
        new_defaults[key] = coerce_value(row.get("valeur") or "")

    new_gery_export: dict[str, Any] = {"enabled": enabled}
    if not enabled:
        new_gery_export["blocked_reason"] = blocked_reason
    new_gery_export["flatten_strategy"] = st.session_state[f"ge_flatten_{pending_id}"]
    for passthrough_key in ("derived_code_template", "description_template"):
        if gery_export.get(passthrough_key):
            new_gery_export[passthrough_key] = gery_export[passthrough_key]
    new_gery_export["defaults"] = new_defaults
    new_gery_export["price_export_mapping"] = {
        "direct_unit_cost": direct_unit_cost.strip() or "installer",
    }

    result: dict[str, Any] = {
        "supplier_code": supplier_code.strip(),
        "mapping_version": int(data.get("mapping_version", 1)),
        "description": description,
        "upload_mode": st.session_state[f"upload_mode_{pending_id}"],
    }
    if "sharepoint_folder" in data:
        result["sharepoint_folder"] = data["sharepoint_folder"]

    if isinstance(sheet_match, dict):
        result["sheet_match"] = sheet_match
    else:
        result["sheet_match"] = st.session_state[f"sheet_match_{pending_id}"]

    result["header_detection"] = {
        "mode": header_detection.get("mode", "explicit"),
        "row": int(header_row),
    }
    result["data_starts_row"] = int(data_starts_row)
    result["extraction_mode"] = "table"
    if "product_kind" in data:
        result["product_kind"] = data["product_kind"]

    if new_row_filter:
        result["row_filter"] = new_row_filter
    result["columns"] = new_columns
    if new_prices:
        result["prices"] = new_prices
    if new_attributes:
        result["attributes"] = new_attributes
    if new_file_metadata:
        result["file_metadata"] = new_file_metadata
    result["gery_export"] = new_gery_export

    return result


# ─── Application ────────────────────────────────────────────────────────────

st.title("Validation des mappings fournisseurs générés par IA")

if "last_action_html" in st.session_state:
    show_action_result(st.session_state.pop("last_action_html"))

try:
    pending_items = fetch_pending_list()
except Exception as exc:
    st.error(f"Impossible de contacter l'API ({API_URL}) : {exc}")
    st.stop()

status_options = ["pending", "approved", "rejected", "tous"]
status_filter = st.sidebar.selectbox("Statut", status_options, index=0)

filtered = pending_items if status_filter == "tous" else [
    item for item in pending_items if item["status"] == status_filter
]

if not filtered:
    st.sidebar.info("Aucune demande pour ce filtre.")
    st.info("Aucune demande à traiter pour le moment.")
    st.stop()

labels = [
    f"{item['filename']} — {item['supplier_guess']} ({item['status']})" for item in filtered
]
selected_idx = st.sidebar.radio(
    "Demandes", options=range(len(filtered)), format_func=lambda i: labels[i]
)
pending_id = filtered[selected_idx]["id"]

meta = fetch_detail(pending_id)

st.subheader(meta["filename"])
c1, c2, c3, c4 = st.columns(4)
c1.metric("Fournisseur deviné", meta["supplier_guess"])
c2.metric("Dossier SharePoint", meta["folder_name"])
c3.metric("Statut", meta["status"])
c4.metric("Créé le", meta["created_at"][:19].replace("T", " "))

with st.expander("Aperçu du fichier Excel"):
    st.code(fetch_preview(pending_id), language=None)

yaml_key = f"yaml_text_{pending_id}"
if yaml_key not in st.session_state:
    st.session_state[yaml_key] = meta["yaml_proposed"]

tab_yaml, tab_form = st.tabs(["YAML", "Formulaire simplifié"])

with tab_yaml:
    st.text_area("Mapping YAML", key=yaml_key, height=500)
    if st.button("Enregistrer le YAML", key=f"save_yaml_{pending_id}"):
        resp = api_put(f"/api/v1/review/{pending_id}", {"yaml_content": st.session_state[yaml_key]})
        if resp.status_code == 200:
            st.success("YAML enregistré et validé.")
        elif resp.status_code == 422:
            st.error("YAML invalide :")
            for err in resp.json().get("detail", []):
                st.write(f"- {err}")
        else:
            st.error(f"Erreur {resp.status_code} : {resp.text}")

with tab_form:
    current_data = load_yaml(st.session_state[yaml_key])
    extraction_mode = current_data.get("extraction_mode")
    if extraction_mode != "table":
        st.info(
            f"Le formulaire simplifié n'est disponible que pour le mode d'extraction "
            f"'table'. Ce fichier est en mode '{extraction_mode}'. Utilisez l'onglet YAML."
        )
    else:
        new_mapping = render_table_form(current_data, pending_id)
        if new_mapping is not None:
            new_yaml_text = dump_yaml(new_mapping)
            resp = api_put(f"/api/v1/review/{pending_id}", {"yaml_content": new_yaml_text})
            if resp.status_code == 200:
                st.session_state[yaml_key] = new_yaml_text
                st.success("Formulaire enregistré et validé.")
                st.rerun()
            elif resp.status_code == 422:
                st.error("Configuration invalide :")
                for err in resp.json().get("detail", []):
                    st.write(f"- {err}")
            else:
                st.error(f"Erreur {resp.status_code} : {resp.text}")

st.divider()

disabled = meta["status"] != "pending"
action_col1, action_col2 = st.columns(2)

if action_col1.button(
    "✅ Valider et générer les exports",
    disabled=disabled,
    type="primary",
    key=f"approve_{pending_id}",
):
    resp = api_get(f"/api/v1/review/{pending_id}/approve")
    st.session_state["last_action_html"] = resp.text
    st.rerun()

if action_col2.button("❌ Rejeter", disabled=disabled, key=f"reject_{pending_id}"):
    resp = api_get(f"/api/v1/review/{pending_id}/reject")
    st.session_state["last_action_html"] = resp.text
    st.rerun()
