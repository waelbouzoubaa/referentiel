"""Interface de validation des mappings YAML générés par IA pour les fournisseurs inconnus.

Permet de relire l'aperçu du fichier Excel source, d'éditer le YAML proposé
(directement ou via un formulaire simplifié pour le mode 'table'), puis de
valider (génère les exports Gery) ou de rejeter la proposition.
"""
from __future__ import annotations

import base64
import io
import os
import re
from pathlib import Path
from typing import Any

import httpx
import streamlit as st
from ruamel.yaml import YAML

_LOGO_PATH = Path(__file__).parent / "logo.png"

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
    "extract_integer",
]

st.set_page_config(page_title="Validation mappings fournisseurs", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Lexend:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"], .stMarkdown, p, label, span, div, button, input, textarea {
        font-family: 'Lexend', sans-serif;
    }
    h1, h2, h3, h4, h5, h6, .stTabs [data-baseweb="tab"] p {
        font-family: 'Lexend', sans-serif !important;
        color: #003D7C;
        font-weight: 600;
    }
    body, .stMarkdown p, label { color: #4A4A49; }

    /* En-tête sobre (filet bleu, pas d'aplat) */
    .app-header {
        display: flex; align-items: center; gap: 16px;
        padding: 2px 0 14px; margin-bottom: 20px;
        border-bottom: 2px solid #003D7C;
    }
    .app-header .wordmark {
        font-size: 28px; font-weight: 700; color: #003D7C; letter-spacing: .5px;
    }
    .app-header .wordmark .dot { color: #D41317; }
    .app-header .titles .t {
        font-size: 15px; font-weight: 600; color: #003D7C; line-height: 1.2;
    }
    .app-header .titles .s { font-size: 12.5px; color: #4A4A49; }

    /* Boutons : bleu Ramery, sobres */
    .stButton > button, .stDownloadButton > button {
        border-radius: 6px; font-weight: 500;
    }
    .stButton > button[kind="primary"], .stDownloadButton > button {
        background: #003D7C; color: #FFFFFF; border: 1px solid #003D7C;
    }
    .stButton > button[kind="primary"]:hover,
    .stDownloadButton > button:hover { background: #002B58; border-color: #002B58; color: #FFFFFF; }

    /* Badges de statut (teintes légères, pas d'aplats lourds) */
    .badge { display: inline-block; padding: 2px 12px; border-radius: 999px;
             font-size: 12px; font-weight: 600; border: 1px solid; }
    .badge-ok   { color: #00695C; background: #E6F4F1; border-color: #009883; }
    .badge-wait { color: #003D7C; background: #EAF1F8; border-color: #003D7C; }
    .badge-ko   { color: #A30F12; background: #FDEAEA; border-color: #D41317; }

    hr { border-color: #E3E8EE; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _logo_data_uri() -> str:
    try:
        data = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
        return f"data:image/png;base64,{data}"
    except Exception:
        return ""


def render_header() -> None:
    """En-tête de marque sobre (charte Ramery) : logo + nom + titre applicatif."""
    logo = _logo_data_uri()
    logo_html = (
        f'<img src="{logo}" alt="Ramery" style="height:44px;width:44px;'
        f'border-radius:8px;object-fit:cover"/>' if logo else ""
    )
    st.markdown(
        f"""
        <div class="app-header">
          {logo_html}
          <span class="wordmark">Ramery</span>
          <div class="titles">
            <div class="t">Référentiel fournisseurs</div>
            <div class="s">Normalisation des catalogues &rarr; export Gery</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_badge(status: str) -> str:
    """Retourne le HTML d'un badge de statut coloré (charte Ramery)."""
    mapping = {
        "approved": ("badge-ok", "validé"),
        "pending": ("badge-wait", "en attente"),
        "rejected": ("badge-ko", "rejeté"),
    }
    cls, label = mapping.get(status, ("badge-wait", status))
    return f'<span class="badge {cls}">{label}</span>'


# ─── Helpers API ────────────────────────────────────────────────────────────

def api_get(path: str) -> httpx.Response:
    return httpx.get(f"{API_URL}{path}", timeout=30)


def api_put(path: str, json_body: dict[str, Any]) -> httpx.Response:
    return httpx.put(f"{API_URL}{path}", json=json_body, timeout=30)


def api_post(path: str, json_body: dict[str, Any]) -> httpx.Response:
    return httpx.post(f"{API_URL}{path}", json=json_body, timeout=60)


def fetch_pending_list() -> list[dict[str, Any]]:
    resp = api_get("/api/v1/review/pending")
    resp.raise_for_status()
    return resp.json()


def fetch_detail(pending_id: str) -> dict[str, Any]:
    resp = api_get(f"/api/v1/review/{pending_id}")
    resp.raise_for_status()
    return resp.json()


def fetch_preview(pending_id: str, sheet: str | None = None) -> str:
    from urllib.parse import quote

    path = f"/api/v1/review/{pending_id}/preview"
    if sheet:
        path += f"?sheet={quote(sheet)}"
    resp = api_get(path)
    if resp.status_code != 200:
        return f"(Aperçu indisponible : {resp.text})"
    return resp.json().get("preview", "")


def fetch_sheets(pending_id: str) -> list[str]:
    resp = api_get(f"/api/v1/review/{pending_id}/sheets")
    if resp.status_code != 200:
        return []
    return resp.json().get("sheets", [])


def fetch_export_bytes(pending_id: str, filename: str) -> bytes:
    resp = api_get(f"/api/v1/review/{pending_id}/download?filename={filename}")
    resp.raise_for_status()
    return resp.content


_DOCS_DIR = Path("/app/docs")
_GITHUB_DOCS = "https://github.com/waelbouzoubaa/referentiel/blob/main/docs"


def render_help_view() -> None:
    """Vue d'aide : affiche les guides (création de YAML + runbook) dans l'app."""
    st.subheader("❓ Aide")
    st.caption("Guides pour créer les mappings et opérer le système.")
    tab_table, tab_matrix, tab_multi, tab_atlantic, tab_runbook = st.tabs([
        "📋 Table simple (Atlantic…)",
        "🔢 Matrix (Airisol…)",
        "📑 Multi-table (Agenor…)",
        "✅ Exemple réel — Atlantic",
        "🛠️ Runbook",
    ])
    with tab_table:
        doc = _DOCS_DIR / "GUIDE_YAML_TABLE.md"
        if doc.exists():
            st.markdown(doc.read_text(encoding="utf-8"))
        else:
            st.info("Guide non monté dans le conteneur.")
            st.link_button("Ouvrir sur GitHub", f"{_GITHUB_DOCS}/GUIDE_YAML_TABLE.md")
    with tab_matrix:
        doc = _DOCS_DIR / "GUIDE_YAML_MATRIX.md"
        if doc.exists():
            st.markdown(doc.read_text(encoding="utf-8"))
        else:
            st.info("Guide non monté dans le conteneur.")
            st.link_button("Ouvrir sur GitHub", f"{_GITHUB_DOCS}/GUIDE_YAML_MATRIX.md")
    with tab_multi:
        doc = _DOCS_DIR / "GUIDE_YAML_MULTI_TABLE.md"
        if doc.exists():
            st.markdown(doc.read_text(encoding="utf-8"))
        else:
            st.info("Guide non monté dans le conteneur.")
            st.link_button("Ouvrir sur GitHub", f"{_GITHUB_DOCS}/GUIDE_YAML_MULTI_TABLE.md")
    with tab_atlantic:
        doc = _DOCS_DIR / "EXAMPLE_ATLANTIC.md"
        if doc.exists():
            st.markdown(doc.read_text(encoding="utf-8"))
        else:
            st.info("Exemple non monté dans le conteneur.")
            st.link_button("Ouvrir sur GitHub", f"{_GITHUB_DOCS}/EXAMPLE_ATLANTIC.md")
    with tab_runbook:
        doc = _DOCS_DIR / "RUNBOOK.md"
        if doc.exists():
            st.markdown(doc.read_text(encoding="utf-8"))
        else:
            st.info("Runbook non monté dans le conteneur.")
            st.link_button("Ouvrir sur GitHub", f"{_GITHUB_DOCS}/RUNBOOK.md")


def render_exports_view() -> None:
    """Vue de consultation et téléchargement des exports Gery, groupés par fournisseur."""
    st.subheader("📤 Exports Gery générés")
    st.caption(
        "Fichiers CSV produits automatiquement, organisés par dossier fournisseur. "
        "Cliquez sur 'YAML' pour voir la configuration appliquée."
    )
    try:
        resp = api_get("/api/v1/exports")
        resp.raise_for_status()
        exports = resp.json()
    except Exception as exc:
        st.error(f"Impossible de récupérer les exports : {exc}")
        return
    if not exports:
        st.info("Aucun export généré pour le moment.")
        return

    # Grouper par dossier fournisseur
    folders: dict[str, list[dict]] = {}
    for e in exports:
        folders.setdefault(e["folder"], []).append(e)

    for folder, items in sorted(folders.items()):
        with st.expander(f"📁 {folder.upper()} — {len(items)} export(s)", expanded=True):
            h1, h2, h3, h4, h5 = st.columns([4, 1, 2, 1, 1])
            h1.markdown("**Fichier**")
            h2.markdown("**Lignes**")
            h3.markdown("**Généré le**")
            h4.markdown("**CSV**")
            h5.markdown("**YAML**")
            for e in items:
                c1, c2, c3, c4, c5 = st.columns([4, 1, 2, 1, 1])
                c1.write(e["filename"])
                c2.write(str(e["line_count"]))
                c3.write(e["modified_at"][:19].replace("T", " "))
                try:
                    data = api_get(f"/api/v1/exports/{e['folder']}/{e['filename']}/download").content
                    c4.download_button("⬇️", data=data, file_name=e["filename"],
                                       mime="text/csv", key=f"dlx_{e['folder']}_{e['filename']}")
                except Exception:
                    c4.warning("—")
                if c5.button("📋", key=f"yaml_{e['folder']}_{e['filename']}", help="Voir le YAML appliqué"):
                    st.session_state[f"show_yaml_{e['folder']}_{e['filename']}"] = True

            # Affichage du YAML si demandé
            for e in items:
                key = f"show_yaml_{e['folder']}_{e['filename']}"
                if st.session_state.get(key):
                    try:
                        yaml_resp = api_get(f"/api/v1/exports/{e['folder']}/{e['filename']}/yaml")
                        if yaml_resp.status_code == 200:
                            yaml_data = yaml_resp.json()
                            st.markdown(f"**YAML appliqué pour `{yaml_data['supplier_code']}`**")
                            st.code(yaml_data["yaml_content"], language="yaml")
                        else:
                            st.warning("YAML introuvable pour cet export.")
                    except Exception as exc:
                        st.error(f"Erreur : {exc}")
                    if st.button("Fermer", key=f"close_yaml_{e['folder']}_{e['filename']}"):
                        st.session_state[key] = False
                        st.rerun()


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


def date_transform_selector(pending_id: str, current: str | None, suffix: str = "") -> str:
    """Sélecteur du format des dates de validité → renvoie le transform à utiliser."""
    options = {"Français — JJ/MM/AAAA": "parse_date_fr", "ISO — AAAA-MM-JJ": "parse_date_iso"}
    labels = list(options)
    cur = current or "parse_date_fr"
    default_label = next((lbl for lbl, val in options.items() if val == cur), labels[0])
    chosen = st.selectbox(
        "Format des dates de validité",
        labels,
        index=labels.index(default_label),
        key=f"datefmt_{pending_id}_{suffix}",
        help="JJ/MM/AAAA pour des dates type 01/02/2024 ; ISO pour 2024-02-01.",
    )
    return options[chosen]


# ─── Formulaire simplifié (mode table) ─────────────────────────────────────

def render_table_form(
    data: dict[str, Any], pending_id: str, columns: list[tuple[str, str]] | None = None
) -> dict[str, Any] | None:
    """Affiche le formulaire simplifié pour un mapping en mode 'table'.

    Returns:
        Le dict de mapping reconstruit si l'utilisateur a cliqué "Enregistrer", sinon None.
    """
    render_columns_reference(columns)
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

    with st.expander("⚙️ Options avancées (attributs & validité)", expanded=False):
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
        date_tr = date_transform_selector(
            pending_id, vs.get("transform") or ve.get("transform"), suffix="table"
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
        new_file_metadata["validity_start"] = {"cell": vs_cell.strip(), "transform": date_tr}
    else:
        new_file_metadata.pop("validity_start", None)
    if ve_cell.strip():
        new_file_metadata["validity_end"] = {"cell": ve_cell.strip(), "transform": date_tr}
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


# ─── Formulaire simplifié v2 — orienté métier, sans YAML (mode table) ──────

_SRC_COLUMN = "Colonne du fichier"
_SRC_CELL = "Cellule unique (cartouche)"
_SRC_FIXED = "Valeur fixe"
_SRC_NONE = "Non renseigné"
_SRC_TEMPLATE = "Modèle calculé (texte + {variables})"


def _column_choices(columns: list[tuple[str, str]]) -> tuple[list[str], dict[str, str]]:
    """Options de liste déroulante 'lettre — en-tête' → lettre de colonne."""
    labels: list[str] = []
    by_label: dict[str, str] = {}
    for letter, header in columns:
        label = f"{letter} — {header}" if header else letter
        labels.append(label)
        by_label[label] = letter
    return labels, by_label


def _pick_column_widget(container, label, columns, current, key):
    """Liste déroulante de colonne détectée (repli en texte libre si aucune détectée)."""
    labels, by_label = _column_choices(columns)
    if not labels:
        return container.text_input(label, value=current or "", key=key)
    default_label = next((lbl for lbl, ltr in by_label.items() if ltr == current), labels[0])
    idx = labels.index(default_label) if default_label in labels else 0
    chosen = container.selectbox(label, labels, index=idx, key=key)
    return by_label[chosen]


def _source_field(
    gery_label: str,
    help_text: str,
    pending_id: str,
    field_id: str,
    allowed_sources: list[str],
    columns: list[tuple[str, str]],
    current_source: str,
    current_value: str,
    extra_render=None,
):
    """Bloc 'nom de colonne Gery + sélecteur de source + champ dépendant'.

    Returns:
        (type_source, valeur, extra) — extra = résultat de extra_render (ex: format de date).
    """
    st.markdown(f"**{gery_label}**")
    if help_text:
        st.caption(help_text)
    left, right = st.columns([1, 2])
    idx = allowed_sources.index(current_source) if current_source in allowed_sources else 0
    source = left.selectbox(
        "Source", allowed_sources, index=idx,
        key=f"{field_id}_src_{pending_id}", label_visibility="collapsed",
    )
    value = ""
    extra = None
    if source == _SRC_COLUMN:
        value = _pick_column_widget(
            right, "Colonne", columns, current_value, key=f"{field_id}_col_{pending_id}"
        )
    elif source == _SRC_CELL:
        value = right.text_input(
            "Cellule (ex: C4)", value=current_value, key=f"{field_id}_cell_{pending_id}"
        )
        if extra_render:
            extra = extra_render(right, field_id)
    elif source == _SRC_FIXED:
        value = right.text_input(
            "Valeur", value=current_value, key=f"{field_id}_fixed_{pending_id}"
        )
    elif source == _SRC_TEMPLATE:
        value = right.text_input(
            "Modèle (ex: {designation} | EP{epaisseur})",
            value=current_value, key=f"{field_id}_tpl_{pending_id}",
        )
    st.divider()
    return source, value.strip() if isinstance(value, str) else value, extra


def render_table_form_simple(
    data: dict[str, Any],
    pending_id: str,
    columns: list[tuple[str, str]],
    sheets: list[str],
    supplier_guess: str = "",
) -> dict[str, Any]:
    """Formulaire orienté métier : associe chaque colonne Gery à sa source dans le fichier.

    Contrairement à `render_table_form`, aucune connaissance YAML n'est requise — que
    des listes déroulantes basées sur les colonnes réellement détectées. Retourne le
    mapping reconstruit à CHAQUE interaction (pas de bouton "Enregistrer") pour piloter
    un aperçu qui se met à jour en direct.
    """
    st.caption(
        "Pour chaque colonne de l'export Gery, indique d'où vient la valeur dans le "
        "fichier fournisseur. L'aperçu à droite se met à jour automatiquement."
    )

    existing_columns = data.get("columns") or {}
    file_metadata = data.get("file_metadata") or {}
    gery_export = data.get("gery_export") or {}
    defaults = gery_export.get("defaults") or {}
    attributes = data.get("attributes") or []
    unit_attr = next((a for a in attributes if a and a.get("key") == "unit_of_measure"), {})
    prices = data.get("prices") or []
    price_export_mapping = gery_export.get("price_export_mapping") or {}
    current_price_type = price_export_mapping.get("direct_unit_cost")
    current_price = next(
        (p for p in prices if p.get("type") == current_price_type), (prices[0] if prices else {})
    )

    # ── Repérage dans le fichier ────────────────────────────────────────────
    st.markdown("##### 📍 Où sont les données dans le fichier ?")
    c1, c2 = st.columns(2)
    current_sheet = data.get("sheet_match") if isinstance(data.get("sheet_match"), str) else None
    sheet_options = sheets or ([current_sheet] if current_sheet else [])
    if sheet_options:
        idx = sheet_options.index(current_sheet) if current_sheet in sheet_options else 0
        sheet_match = c1.selectbox(
            "Feuille Excel", sheet_options, index=idx, key=f"sheet_simple_{pending_id}"
        )
    else:
        sheet_match = c1.text_input(
            "Feuille Excel", value=current_sheet or "", key=f"sheet_simple_{pending_id}"
        )

    data_starts_row = c2.number_input(
        "À quelle ligne commencent les produits ?",
        min_value=2, step=1,
        value=int(data.get("data_starts_row") or 2),
        key=f"data_starts_simple_{pending_id}",
        help="La ligne juste au-dessus est utilisée comme ligne d'en-têtes.",
    )
    supplier_code = st.text_input(
        "Code fournisseur *(obligatoire — identifiant interne, sans espace ni accent)*",
        value=data.get("supplier_code") or supplier_guess or "",
        key=f"supplier_code_simple_{pending_id}",
    )
    if not supplier_code.strip():
        st.warning(
            "⚠️ Code fournisseur vide — les exports ne seront pas correctement nommés/rangés "
            "tant que ce champ n'est pas rempli."
        )

    st.divider()
    st.markdown("##### 🧾 Colonnes de l'export Gery")

    st.markdown("**Code Fournisseur SAGE**")
    st.caption("🔒 Résolu automatiquement à partir du code fournisseur — non modifiable ici.")
    st.divider()

    _, code_col, _ = _source_field(
        "Code article Frns *(obligatoire)*", "",
        pending_id, "supplier_product_code", [_SRC_COLUMN], columns,
        _SRC_COLUMN, (existing_columns.get("supplier_product_code") or {}).get("source_col") or "",
    )

    _, designation_col, _ = _source_field(
        "Description *(obligatoire)*", "",
        pending_id, "designation", [_SRC_COLUMN], columns,
        _SRC_COLUMN, (existing_columns.get("designation") or {}).get("source_col") or "",
    )

    generic_current = (
        (_SRC_COLUMN, (existing_columns.get("generic_code") or {}).get("source_col") or "")
        if existing_columns.get("generic_code") else
        (_SRC_CELL, (file_metadata.get("ramery_generic_code") or {}).get("cell") or "")
        if file_metadata.get("ramery_generic_code") else
        (_SRC_FIXED, defaults.get("article_generique") or "")
        if defaults.get("article_generique") else
        (_SRC_NONE, "")
    )
    generic_src, generic_val, _ = _source_field(
        "Article générique associé",
        "Variable selon le produit (colonne) ou fixe pour tout le fichier (cartouche / valeur).",
        pending_id, "generic", [_SRC_COLUMN, _SRC_CELL, _SRC_FIXED, _SRC_NONE], columns,
        generic_current[0], generic_current[1],
    )

    unit_current = (
        (_SRC_COLUMN, unit_attr.get("source_col") or "")
        if unit_attr else
        (_SRC_FIXED, defaults.get("unit_of_measure") or "U")
    )
    unit_src, unit_val, _ = _source_field(
        "Unité", "",
        pending_id, "unit", [_SRC_COLUMN, _SRC_FIXED], columns,
        unit_current[0], unit_current[1],
    )

    def _date_format_picker(container, field_id):
        options = {"JJ/MM/AAAA": "parse_date_fr", "AAAA-MM-JJ": "parse_date_iso"}
        labels = list(options)
        _vs_or_ve = file_metadata.get("validity_start") or file_metadata.get("validity_end") or {}
        current_fmt = _vs_or_ve.get("transform") or "parse_date_fr"
        default_label = next((lbl for lbl, v in options.items() if v == current_fmt), labels[0])
        chosen = container.selectbox(
            "Format de la date", labels, index=labels.index(default_label),
            key=f"datefmt_simple_{field_id}_{pending_id}",
        )
        return options[chosen]

    vs_current = (_SRC_CELL, (file_metadata.get("validity_start") or {}).get("cell") or "") \
        if file_metadata.get("validity_start") else (_SRC_NONE, "")
    vs_src, vs_val, date_fmt = _source_field(
        "Starting Date (début de validité)", "",
        pending_id, "validity_start", [_SRC_CELL, _SRC_NONE], columns,
        vs_current[0], vs_current[1], extra_render=_date_format_picker,
    )

    ve_current = (_SRC_CELL, (file_metadata.get("validity_end") or {}).get("cell") or "") \
        if file_metadata.get("validity_end") else (_SRC_NONE, "")
    ve_src, ve_val, ve_date_fmt = _source_field(
        "Ending Date (fin de validité)", "",
        pending_id, "validity_end", [_SRC_CELL, _SRC_NONE], columns,
        ve_current[0], ve_current[1], extra_render=_date_format_picker,
    )
    date_fmt = date_fmt or ve_date_fmt or "parse_date_fr"

    st.markdown("**Minimum Quantity**")
    st.caption("Toujours une valeur fixe (identique pour toutes les lignes).")
    min_qty = st.number_input(
        "Quantité minimum", min_value=1, step=1,
        value=int(defaults.get("minimum_quantity") or 1),
        key=f"min_qty_simple_{pending_id}", label_visibility="collapsed",
    )
    st.divider()

    def _decimal_format_picker(container):
        options = {
            "Virgule française (1234,56)": "parse_decimal_fr",
            "Point US (1234.56)": "parse_decimal_us",
        }
        labels = list(options)
        current_fmt = current_price.get("transform") or "parse_decimal_fr"
        default_label = next((lbl for lbl, v in options.items() if v == current_fmt), labels[0])
        chosen = container.selectbox(
            "Format du nombre", labels, index=labels.index(default_label),
            key=f"decimalfmt_simple_{pending_id}",
        )
        return options[chosen]

    st.markdown("**Direct Unit Cost** *(obligatoire)*")
    left, right = st.columns([1, 2])
    left.selectbox(
        "Source", [_SRC_COLUMN], index=0,
        key=f"price_src_{pending_id}", label_visibility="collapsed", disabled=True,
    )
    price_col = _pick_column_widget(
        right, "Colonne", columns, current_price.get("source_col") or "",
        key=f"price_col_{pending_id}",
    )
    decimal_fmt = _decimal_format_picker(right)
    st.divider()

    siren_current = (_SRC_CELL, (file_metadata.get("siren_fournisseur") or {}).get("cell") or "") \
        if file_metadata.get("siren_fournisseur") else (_SRC_NONE, "")
    siren_src, siren_val, _ = _source_field(
        "SIREN Fournisseur", "",
        pending_id, "siren", [_SRC_CELL, _SRC_NONE], columns,
        siren_current[0], siren_current[1],
    )

    with st.expander("⚙️ Optionnel — famille, sous-famille, lignes à exclure", expanded=False):
        family_current = (
            (_SRC_COLUMN, (existing_columns.get("family") or {}).get("source_col") or "")
            if (existing_columns.get("family") or {}).get("source_col") else
            (_SRC_FIXED, (existing_columns.get("family") or {}).get("constant") or "")
            if (existing_columns.get("family") or {}).get("constant") else
            (_SRC_NONE, "")
        )
        family_src, family_val, _ = _source_field(
            "Famille", "",
            pending_id, "family", [_SRC_COLUMN, _SRC_FIXED, _SRC_NONE], columns,
            family_current[0], family_current[1],
        )
        subfamily_current = (
            (_SRC_COLUMN, (existing_columns.get("subfamily") or {}).get("source_col") or "")
            if (existing_columns.get("subfamily") or {}).get("source_col") else
            (_SRC_FIXED, (existing_columns.get("subfamily") or {}).get("constant") or "")
            if (existing_columns.get("subfamily") or {}).get("constant") else
            (_SRC_NONE, "")
        )
        subfamily_src, subfamily_val, _ = _source_field(
            "Sous-famille", "",
            pending_id, "subfamily", [_SRC_COLUMN, _SRC_FIXED, _SRC_NONE], columns,
            subfamily_current[0], subfamily_current[1],
        )
        row_filter = data.get("row_filter") or {}
        exclude_starts_str = st.text_input(
            "Ignorer les lignes qui commencent par (séparées par des virgules)",
            value=", ".join(row_filter.get("exclude_if_starts_with", [])),
            key=f"exclude_starts_simple_{pending_id}",
            help="Utile pour ignorer des lignes de titre ou de mentions légales.",
        )

    # ── Reconstruction du mapping ────────────────────────────────────────────
    new_columns: dict[str, Any] = {
        "supplier_product_code": {
            "source_col": code_col, "transform": ["strip", "to_uppercase"], "required": True,
        },
        "designation": {"source_col": designation_col, "transform": "strip", "required": True},
    }
    new_file_metadata: dict[str, Any] = {}
    new_defaults: dict[str, Any] = dict(defaults)
    new_attributes: list[dict[str, Any]] = []

    if generic_src == _SRC_COLUMN and generic_val:
        new_columns["generic_code"] = {"source_col": generic_val}
        new_defaults.pop("article_generique", None)
    elif generic_src == _SRC_CELL and generic_val:
        new_file_metadata["ramery_generic_code"] = {"cell": generic_val}
        new_defaults.pop("article_generique", None)
    elif generic_src == _SRC_FIXED and generic_val:
        new_defaults["article_generique"] = generic_val

    if unit_src == _SRC_COLUMN and unit_val:
        new_attributes.append(
            {"key": "unit_of_measure", "source_col": unit_val, "data_type": "string"}
        )
        new_defaults["unit_of_measure"] = "U"
    elif unit_src == _SRC_FIXED:
        new_defaults["unit_of_measure"] = unit_val or "U"

    if vs_src == _SRC_CELL and vs_val:
        new_file_metadata["validity_start"] = {"cell": vs_val, "transform": date_fmt}
    if ve_src == _SRC_CELL and ve_val:
        new_file_metadata["validity_end"] = {"cell": ve_val, "transform": date_fmt}
    if siren_src == _SRC_CELL and siren_val:
        new_file_metadata["siren_fournisseur"] = {"cell": siren_val}

    if family_src == _SRC_COLUMN and family_val:
        new_columns["family"] = {"source_col": family_val, "transform": "strip"}
    elif family_src == _SRC_FIXED and family_val:
        new_columns["family"] = {"constant": family_val}
    if subfamily_src == _SRC_COLUMN and subfamily_val:
        new_columns["subfamily"] = {"source_col": subfamily_val, "transform": "strip"}
    elif subfamily_src == _SRC_FIXED and subfamily_val:
        new_columns["subfamily"] = {"constant": subfamily_val}

    new_row_filter: dict[str, Any] = {"must_have_value_in": [code_col]} if code_col else {}
    exclude_starts = [c.strip() for c in exclude_starts_str.split(",") if c.strip()]
    if exclude_starts:
        new_row_filter["exclude_if_starts_with"] = exclude_starts

    new_defaults.setdefault("minimum_quantity", int(min_qty))
    new_defaults["minimum_quantity"] = int(min_qty)
    # Champs Gery pas encore utilisés dans le CSV actuel — valeurs neutres fixes.
    for k, v in {
        "item_purchase_type": "Catalogue", "code_tva": "TVA20", "purchase_type": "Direct",
        "gen_prod_posting_group": "", "job_cost_code": "", "tree_code": "",
        "master_code": "", "item_category_code": "", "product_group_code": "",
    }.items():
        new_defaults.setdefault(k, v)

    result: dict[str, Any] = {
        "supplier_code": supplier_code.strip(),
        "mapping_version": int(data.get("mapping_version", 1)),
        "description": data.get("description", ""),
        "upload_mode": data.get("upload_mode", "full"),
    }
    if "sharepoint_folder" in data:
        result["sharepoint_folder"] = data["sharepoint_folder"]
    if "filename_keywords" in data:
        result["filename_keywords"] = data["filename_keywords"]
    result["sheet_match"] = sheet_match
    result["header_detection"] = {"mode": "explicit", "row": int(data_starts_row) - 1}
    result["data_starts_row"] = int(data_starts_row)
    result["extraction_mode"] = "table"
    if new_row_filter:
        result["row_filter"] = new_row_filter
    result["columns"] = new_columns
    result["prices"] = [{
        "type": "gery", "source_col": price_col, "transform": decimal_fmt, "currency": "EUR",
    }] if price_col else []
    if new_attributes:
        result["attributes"] = new_attributes
    if new_file_metadata:
        result["file_metadata"] = new_file_metadata
    result["gery_export"] = {
        "enabled": True,
        "flatten_strategy": "cartesian",
        "derived_code_template": "{supplier_product_code}",
        "defaults": new_defaults,
        "price_export_mapping": {"direct_unit_cost": "gery"},
    }
    return result


# ─── Colonnes détectées (aide non-technique) ───────────────────────────────

def _col_letter(idx0: int) -> str:
    """Index 0-based → lettre de colonne Excel (0→A, 1→B, 26→AA)."""
    s = ""
    n = idx0
    while n >= 0:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
    return s


def parse_detected_columns(preview: str, header_row: int | None) -> list[tuple[str, str]]:
    """Extrait (lettre, en-tête) de la ligne d'en-tête depuis l'aperçu Excel."""
    if not preview or not header_row:
        return []
    target = f"Ligne {int(header_row):02d}:"
    for line in preview.splitlines():
        if line.startswith(target):
            cells = line.split(":", 1)[1].split("\t")
            return [(_col_letter(i), c.strip()) for i, c in enumerate(cells)]
    return []


def render_columns_reference(columns: list[tuple[str, str]] | None) -> None:
    """Affiche un tableau de référence lettre → en-tête, pour mapper sans deviner."""
    if not columns:
        return
    with st.expander("📋 Colonnes détectées (lettre → en-tête)", expanded=True):
        st.dataframe(
            [{"Colonne": letter, "En-tête": header} for letter, header in columns if header],
            use_container_width=True,
            hide_index=True,
        )


def render_excel_grid(preview_text: str) -> None:
    """Affiche l'aperçu Excel comme une vraie grille : colonnes A,B,C… + n° de lignes.

    Permet à l'utilisateur de repérer une info par sa position (« ligne 6, colonne D »).
    """
    import pandas as pd

    parsed: list[tuple[int, list[str]]] = []
    for line in preview_text.splitlines():
        if not line.startswith("Ligne "):
            continue
        prefix, sep, rest = line.partition(": ")
        if not sep:
            continue
        try:
            row_num = int(prefix.replace("Ligne", "").strip())
        except ValueError:
            continue
        parsed.append((row_num, rest.split("\t")))

    if not parsed:
        st.info("Aperçu indisponible.")
        return

    max_cols = max(len(cells) for _, cells in parsed)
    col_letters = [_col_letter(i) for i in range(max_cols)]
    records = [cells + [""] * (max_cols - len(cells)) for _, cells in parsed]
    index = [row_num for row_num, _ in parsed]
    df = pd.DataFrame(records, columns=col_letters, index=index)
    st.dataframe(df, use_container_width=True, height=460)


# ─── Blocs de formulaire partagés (tous modes) ─────────────────────────────

def _render_general_info(data: dict[str, Any], pending_id: str) -> dict[str, Any]:
    st.markdown("##### Informations générales")
    c1, c2 = st.columns(2)
    supplier_code = c1.text_input("Code fournisseur", value=data.get("supplier_code", ""),
                                  key=f"sc_{pending_id}")
    description = c2.text_input("Description", value=data.get("description", ""),
                                key=f"desc_{pending_id}")
    c3, c4 = st.columns(2)
    modes = ["full", "incremental"]
    um = data.get("upload_mode", "incremental")
    upload_mode = c3.selectbox("Mode d'upload", modes,
                               index=modes.index(um) if um in modes else 1, key=f"um_{pending_id}")
    sheet_match = data.get("sheet_match", "auto")
    sheet_match_val: Any = sheet_match
    if isinstance(sheet_match, dict):
        c4.info("sheet_match avancé — éditable via l'onglet YAML.")
    else:
        sheet_match_val = c4.text_input("Onglet (sheet_match)", value=str(sheet_match),
                                        key=f"sm_{pending_id}")
    hd = data.get("header_detection") or {}
    c5, c6 = st.columns(2)
    header_row = c5.number_input("Ligne d'en-tête", min_value=1, step=1,
                                 value=int(hd.get("row") or 1), key=f"hr_{pending_id}")
    data_starts_row = c6.number_input("Première ligne de données", min_value=1, step=1,
                                      value=int(data.get("data_starts_row") or (header_row + 1)),
                                      key=f"dsr_{pending_id}")

    existing_kw = data.get("filename_keywords") or []
    kw_str = st.text_input(
        "Mots-clés du fichier (séparés par virgules)",
        value=", ".join(existing_kw),
        key=f"kw_{pending_id}",
        help=(
            "Ce YAML ne s'appliquera qu'aux fichiers dont le nom contient au moins un de ces mots. "
            "Laisser vide = s'applique à tous les fichiers du dossier SharePoint. "
            "Ex: Chauffage, chauffage"
        ),
    )

    base: dict[str, Any] = {
        "supplier_code": supplier_code.strip(),
        "mapping_version": int(data.get("mapping_version", 1)),
        "description": description,
        "upload_mode": upload_mode,
        "sharepoint_folder": data.get("sharepoint_folder", ""),
        "filename_keywords": [k.strip() for k in kw_str.split(",") if k.strip()],
        "sheet_match": sheet_match if isinstance(sheet_match, dict) else sheet_match_val,
        "header_detection": {"mode": hd.get("mode", "explicit"), "row": int(header_row)},
        "data_starts_row": int(data_starts_row),
    }
    return base


def _render_attributes(data_list: list[dict[str, Any]] | None, key: str) -> list[dict[str, Any]]:
    rows = []
    for attr in data_list or []:
        attr = attr or {}
        rows.append({
            "key": attr.get("key", ""),
            "source_col": attr.get("source_col", ""),
            "data_type": attr.get("data_type", "string"),
            "unit": attr.get("unit", "") or "",
            "transform": transform_to_str(attr.get("transform")),
        })
    df = st.data_editor(
        rows, num_rows="dynamic", use_container_width=True, key=key,
        column_config={
            "key": st.column_config.TextColumn("Clé attribut"),
            "source_col": st.column_config.TextColumn("Colonne source"),
            "data_type": st.column_config.TextColumn("Type (string/integer/decimal/enum)"),
            "unit": st.column_config.TextColumn("Unité"),
            "transform": st.column_config.TextColumn("Transformation"),
        },
    )
    out = []
    for row in df:
        k = (row.get("key") or "").strip()
        sc = (row.get("source_col") or "").strip()
        if not k or not sc:
            continue
        entry: dict[str, Any] = {
            "key": k, "source_col": sc,
            "data_type": (row.get("data_type") or "string").strip() or "string",
        }
        unit = (row.get("unit") or "").strip()
        if unit:
            entry["unit"] = unit
        tr = transform_from_str(row.get("transform") or "")
        if tr:
            entry["transform"] = tr
        out.append(entry)
    return out


def _render_file_metadata(data: dict[str, Any], pending_id: str) -> dict[str, Any]:
    st.markdown("##### Validité du tarif (optionnel)")
    fm = data.get("file_metadata") or {}
    vs = fm.get("validity_start") or {}
    ve = fm.get("validity_end") or {}
    c1, c2 = st.columns(2)
    vs_cell = c1.text_input("Cellule début de validité", value=vs.get("cell", "") or "",
                            key=f"vs_{pending_id}")
    ve_cell = c2.text_input("Cellule fin de validité", value=ve.get("cell", "") or "",
                            key=f"ve_{pending_id}")
    date_tr = date_transform_selector(
        pending_id, vs.get("transform") or ve.get("transform"), suffix="meta"
    )
    new_fm = dict(fm)
    if vs_cell.strip():
        new_fm["validity_start"] = {"cell": vs_cell.strip(), "transform": date_tr}
    else:
        new_fm.pop("validity_start", None)
    if ve_cell.strip():
        new_fm["validity_end"] = {"cell": ve_cell.strip(), "transform": date_tr}
    else:
        new_fm.pop("validity_end", None)
    return new_fm


def _render_gery_export(data: dict[str, Any], pending_id: str) -> dict[str, Any]:
    st.markdown("##### Export Gery")
    ge = data.get("gery_export") or {}
    enabled = st.checkbox("Export Gery activé", value=ge.get("enabled", True),
                          key=f"ge_en_{pending_id}")
    blocked_reason = ""
    if not enabled:
        blocked_reason = st.text_input("Raison du blocage (obligatoire si désactivé)",
                                       value=ge.get("blocked_reason", "") or "",
                                       key=f"ge_br_{pending_id}")
    strategies = ["cartesian", "best_price_only", "skip_for_review"]
    fs = ge.get("flatten_strategy", "cartesian")
    flatten = st.selectbox("Stratégie de mise à plat", strategies,
                           index=strategies.index(fs) if fs in strategies else 0,
                           key=f"ge_fs_{pending_id}")
    direct_unit_cost = st.text_input(
        "Type de prix utilisé pour 'Direct Unit Cost'",
        value=(ge.get("price_export_mapping") or {}).get("direct_unit_cost", "installer"),
        key=f"ge_duc_{pending_id}",
    )
    derived_tpl = st.text_input("Template de code dérivé (optionnel)",
                                value=ge.get("derived_code_template", "") or "",
                                key=f"ge_dct_{pending_id}")
    st.caption("Valeurs par défaut Gery (clé / valeur)")
    defaults_rows = [{"clé": k, "valeur": str(v)} for k, v in (ge.get("defaults") or {}).items()]
    defaults_df = st.data_editor(
        defaults_rows, num_rows="dynamic", use_container_width=True, key=f"ge_def_{pending_id}",
        column_config={"clé": st.column_config.TextColumn("Clé"),
                       "valeur": st.column_config.TextColumn("Valeur")},
    )
    new_defaults: dict[str, Any] = {}
    for row in defaults_df:
        k = (row.get("clé") or "").strip()
        if k:
            new_defaults[k] = coerce_value(row.get("valeur") or "")

    result: dict[str, Any] = {"enabled": enabled}
    if not enabled:
        result["blocked_reason"] = blocked_reason
    result["flatten_strategy"] = flatten
    if derived_tpl.strip():
        result["derived_code_template"] = derived_tpl.strip()
    result["defaults"] = new_defaults
    result["price_export_mapping"] = {"direct_unit_cost": direct_unit_cost.strip() or "installer"}
    return result


def _render_attributes_dropdown(
    data_list: list[dict[str, Any]] | None, key: str, columns: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """Comme `_render_attributes`, mais « Colonne source » est une liste déroulante des
    colonnes détectées plutôt qu'un champ texte libre à deviner."""
    letters = [letter for letter, _ in columns]
    rows = []
    for attr in data_list or []:
        attr = attr or {}
        sc = attr.get("source_col", "")
        rows.append({
            "key": attr.get("key", ""),
            "source_col": sc if sc in letters else (letters[0] if letters else ""),
            "data_type": attr.get("data_type", "string"),
            "unit": attr.get("unit", "") or "",
            "transform": transform_to_str(attr.get("transform")),
        })
    df = st.data_editor(
        rows, num_rows="dynamic", use_container_width=True, key=key,
        column_config={
            "key": st.column_config.TextColumn("Clé attribut (ex: epaisseur)"),
            "source_col": st.column_config.SelectboxColumn("Colonne source", options=letters or [""]),
            "data_type": st.column_config.SelectboxColumn(
                "Type", options=["string", "integer", "decimal", "enum", "duration"]
            ),
            "unit": st.column_config.TextColumn("Unité"),
            "transform": st.column_config.TextColumn("Transformation"),
        },
    )
    out = []
    for row in df:
        k = (row.get("key") or "").strip()
        sc = (row.get("source_col") or "").strip()
        if not k or not sc:
            continue
        entry: dict[str, Any] = {
            "key": k, "source_col": sc,
            "data_type": (row.get("data_type") or "string").strip() or "string",
        }
        unit = (row.get("unit") or "").strip()
        if unit:
            entry["unit"] = unit
        tr = transform_from_str(row.get("transform") or "")
        if tr:
            entry["transform"] = tr
        out.append(entry)
    return out


# ─── Formulaire simplifié v2 — orienté métier, sans YAML (mode matrix) ─────

def render_matrix_form_simple(
    data: dict[str, Any],
    pending_id: str,
    preview_text: str,
    sheets: list[str],
    supplier_guess: str = "",
) -> dict[str, Any] | None:
    """Formulaire orienté métier pour le mode 'matrix' (grille prix palier × variante).

    Même esprit que `render_table_form_simple` : dropdowns sur colonnes réellement
    détectées, pas de lettre à deviner ni de plage à taper, aperçu recalculé en
    direct (pas de bouton « Enregistrer »).
    """
    st.caption(
        "Pour les fichiers en grille de prix (paliers de quantité × variantes, ex. "
        "Airisol). Indique où sont les colonnes produit et où est la grille de prix — "
        "l'aperçu à droite se met à jour automatiquement."
    )

    existing_pc = data.get("product_columns") or {}
    file_metadata = data.get("file_metadata") or {}
    gery_export = data.get("gery_export") or {}
    defaults = gery_export.get("defaults") or {}
    dz = data.get("data_zone") or {}
    pm = data.get("price_matrix") or {}
    ta = pm.get("tier_axis") or {}
    va = pm.get("variant_axis") or {}
    existing_groups = pm.get("column_groups") or []

    # ── Repérage dans le fichier ────────────────────────────────────────────
    st.markdown("##### 📍 Où sont les données dans le fichier ?")
    c1, c2 = st.columns(2)
    current_sheet = data.get("sheet_match") if isinstance(data.get("sheet_match"), str) else None
    sheet_options = sheets or ([current_sheet] if current_sheet else [])
    if sheet_options:
        idx = sheet_options.index(current_sheet) if current_sheet in sheet_options else 0
        sheet_match = c1.selectbox(
            "Feuille Excel", sheet_options, index=idx, key=f"sheet_matrix_{pending_id}"
        )
    else:
        sheet_match = c1.text_input(
            "Feuille Excel", value=current_sheet or "", key=f"sheet_matrix_{pending_id}"
        )
    supplier_code = c2.text_input(
        "Code fournisseur *(obligatoire)*",
        value=data.get("supplier_code") or supplier_guess or "",
        key=f"supplier_code_matrix_{pending_id}",
    )
    if not supplier_code.strip():
        st.warning(
            "⚠️ Code fournisseur vide — les exports ne seront pas correctement nommés/rangés."
        )

    c3, c4 = st.columns(2)
    variant_header_row = c3.number_input(
        "Ligne d'en-tête (variantes + colonnes produit)", min_value=1, step=1,
        value=int(va.get("header_row") or (data.get("header_detection") or {}).get("row") or 1),
        key=f"va_hr_simple_{pending_id}",
        help="La ligne où sont écrits les noms de variantes (ALU, BLANC…), juste au-dessus des produits.",
    )
    tier_header_row = c4.number_input(
        "Ligne d'en-tête des paliers", min_value=1, step=1,
        value=int(ta.get("header_row") or max(1, int(variant_header_row) - 1)),
        key=f"ta_hr_simple_{pending_id}",
        help="La ligne juste au-dessus, avec les paliers de quantité (souvent fusionnée sur plusieurs colonnes).",
    )

    _existing_rows = (dz.get("rows") or "").split(":")
    _existing_row_start = (
        int(_existing_rows[0]) if len(_existing_rows) == 2 and _existing_rows[0].strip().isdigit() else None
    )
    _existing_row_end = (
        int(_existing_rows[1]) if len(_existing_rows) == 2 and _existing_rows[1].strip().isdigit() else None
    )
    c5, c6 = st.columns(2)
    data_starts_row = c5.number_input(
        "Première ligne de produits", min_value=2, step=1,
        value=int(data.get("data_starts_row") or _existing_row_start or (int(variant_header_row) + 1)),
        key=f"dsr_matrix_simple_{pending_id}",
    )
    data_ends_row = c6.number_input(
        "Dernière ligne de produits", min_value=int(data_starts_row), step=1,
        value=max(int(data_starts_row), int(_existing_row_end or (int(data_starts_row) + 20))),
        key=f"der_matrix_simple_{pending_id}",
        help="Dernière ligne du tableau (avant totaux/mentions légales éventuels).",
    )

    detected_columns = parse_detected_columns(preview_text, int(variant_header_row))
    tier_ref_columns = parse_detected_columns(preview_text, int(tier_header_row))
    tier_ref_by_letter = dict(tier_ref_columns)

    st.divider()
    st.markdown("##### 🧾 Colonnes produit")

    _, designation_col, _ = _source_field(
        "Désignation *(obligatoire)*", "",
        pending_id, "mx_designation", [_SRC_COLUMN], detected_columns,
        _SRC_COLUMN, (existing_pc.get("designation") or {}).get("source_col") or "",
    )

    _existing_code = existing_pc.get("supplier_product_code") or {}
    if _existing_code.get("derived_from"):
        code_current = (_SRC_TEMPLATE, _existing_code["derived_from"])
    elif _existing_code.get("source_col"):
        code_current = (_SRC_COLUMN, _existing_code["source_col"])
    else:
        code_current = (_SRC_TEMPLATE, "{designation}")
    code_src, code_val, _ = _source_field(
        "Code article Frns *(obligatoire)*",
        "Colonne directe, ou modèle calculé si le code n'existe pas tel quel dans le "
        "fichier (ex: {designation} | EP{epaisseur}).",
        pending_id, "mx_code", [_SRC_COLUMN, _SRC_TEMPLATE], detected_columns,
        code_current[0], code_current[1],
    )

    family_current = (
        (_SRC_COLUMN, (existing_pc.get("family") or {}).get("source_col") or "")
        if (existing_pc.get("family") or {}).get("source_col") else
        (_SRC_FIXED, (existing_pc.get("family") or {}).get("constant") or "")
        if (existing_pc.get("family") or {}).get("constant") else
        (_SRC_NONE, "")
    )
    family_src, family_val, _ = _source_field(
        "Famille", "", pending_id, "mx_family", [_SRC_COLUMN, _SRC_FIXED, _SRC_NONE],
        detected_columns, family_current[0], family_current[1],
    )
    subfamily_current = (
        (_SRC_COLUMN, (existing_pc.get("subfamily") or {}).get("source_col") or "")
        if (existing_pc.get("subfamily") or {}).get("source_col") else
        (_SRC_FIXED, (existing_pc.get("subfamily") or {}).get("constant") or "")
        if (existing_pc.get("subfamily") or {}).get("constant") else
        (_SRC_NONE, "")
    )
    subfamily_src, subfamily_val, _ = _source_field(
        "Sous-famille", "", pending_id, "mx_subfamily", [_SRC_COLUMN, _SRC_FIXED, _SRC_NONE],
        detected_columns, subfamily_current[0], subfamily_current[1],
    )

    st.markdown("**Attributs techniques** *(épaisseur, valeur R… utilisés dans le code "
                "article et l'export)*")
    attr_out = _render_attributes_dropdown(
        data.get("attributes"), f"mx_attrs_{pending_id}", detected_columns
    )
    st.divider()

    # ── Grille de prix ──────────────────────────────────────────────────────
    st.markdown("##### 💰 Grille de prix (paliers × variantes)")
    st.caption(
        "Sélectionne les colonnes qui contiennent des prix, puis indique pour chacune "
        "son palier de quantité et sa variante. Les colonnes consécutives partageant le "
        "même palier forment un bloc."
    )
    all_labels, by_label = _column_choices(detected_columns)
    price_letters_existing = [c for g in existing_groups for c in (g.get("columns") or [])]
    default_price_labels = [lbl for lbl in all_labels if by_label[lbl] in price_letters_existing]
    selected_price_labels = st.multiselect(
        "Colonnes de prix", all_labels, default=default_price_labels,
        key=f"price_cols_matrix_{pending_id}",
    )
    selected_letters = [by_label[lbl] for lbl in all_labels if lbl in selected_price_labels]

    existing_tier_by_letter: dict[str, str] = {}
    existing_variant_by_letter: dict[str, str] = {}
    for g in existing_groups:
        cols = g.get("columns") or []
        variants = g.get("variants") or []
        for i, c in enumerate(cols):
            existing_tier_by_letter[c] = g.get("tier_label", "")
            if i < len(variants):
                existing_variant_by_letter[c] = variants[i]

    detected_by_letter = dict(detected_columns)
    grid_rows = [
        {
            "Colonne": letter,
            "Palier": existing_tier_by_letter.get(letter) or tier_ref_by_letter.get(letter, ""),
            "Variante": existing_variant_by_letter.get(letter) or detected_by_letter.get(letter, ""),
        }
        for letter in selected_letters
    ]
    price_grid = st.data_editor(
        grid_rows, num_rows="fixed", use_container_width=True, hide_index=True,
        key=f"price_grid_matrix_{pending_id}",
        column_config={
            "Colonne": st.column_config.TextColumn("Colonne", disabled=True),
            "Palier": st.column_config.TextColumn("Palier (ex: 0-500m²)"),
            "Variante": st.column_config.TextColumn("Variante (ex: ALU)"),
        },
    )

    c7, c8, c9 = st.columns(3)
    variant_dimension_name = c7.text_input(
        "Nom de la dimension variante (ex: couleur, taille)",
        value=va.get("dimension_name", "variante"), key=f"va_dim_simple_{pending_id}",
    )
    tier_fallback_unit = c8.text_input(
        "Unité des paliers (ex: m²)", value=ta.get("fallback_unit", "m²"),
        key=f"ta_unit_simple_{pending_id}",
    )
    currency = c9.text_input(
        "Devise", value=pm.get("currency", "EUR"), key=f"currency_simple_{pending_id}"
    )

    def _decimal_format_picker(container):
        options = {
            "Virgule française (1234,56)": "parse_decimal_fr",
            "Point US (1234.56)": "parse_decimal_us",
        }
        labels = list(options)
        current_fmt = pm.get("transform") or "parse_decimal_fr"
        default_label = next((lbl for lbl, v in options.items() if v == current_fmt), labels[0])
        chosen = container.selectbox(
            "Format du nombre", labels, index=labels.index(default_label),
            key=f"decimalfmt_matrix_{pending_id}",
        )
        return options[chosen]

    decimal_fmt = _decimal_format_picker(st)

    with st.expander("⚙️ Options avancées de la grille", expanded=False):
        detect_per_block = st.checkbox(
            "Lire le palier colonne par colonne (recommandé si les cellules de palier "
            "sont fusionnées)",
            value=bool(ta.get("detect_per_block", True)), key=f"detect_per_block_{pending_id}",
        )
        price_type = st.text_input(
            "Type de prix (interne)", value=pm.get("price_type", "list"),
            key=f"price_type_matrix_{pending_id}",
        )
    st.divider()

    unit_attr = next(
        (a for a in (data.get("attributes") or []) if a and a.get("key") == "unit_of_measure"), {}
    )
    unit_current = (
        (_SRC_COLUMN, unit_attr.get("source_col") or "")
        if unit_attr else
        (_SRC_FIXED, defaults.get("unit_of_measure") or "M2")
    )
    unit_src, unit_val, _ = _source_field(
        "Unité", "", pending_id, "mx_unit", [_SRC_COLUMN, _SRC_FIXED], detected_columns,
        unit_current[0], unit_current[1],
    )

    def _date_format_picker(container, field_id):
        options = {"JJ/MM/AAAA": "parse_date_fr", "AAAA-MM-JJ": "parse_date_iso"}
        labels = list(options)
        _vs_or_ve = file_metadata.get("validity_start") or file_metadata.get("validity_end") or {}
        current_fmt = _vs_or_ve.get("transform") or "parse_date_fr"
        default_label = next((lbl for lbl, v in options.items() if v == current_fmt), labels[0])
        chosen = container.selectbox(
            "Format de la date", labels, index=labels.index(default_label),
            key=f"datefmt_matrix_{field_id}_{pending_id}",
        )
        return options[chosen]

    vs_current = (_SRC_CELL, (file_metadata.get("validity_start") or {}).get("cell") or "") \
        if file_metadata.get("validity_start") else (_SRC_NONE, "")
    vs_src, vs_val, date_fmt = _source_field(
        "Starting Date (début de validité)", "", pending_id, "mx_validity_start",
        [_SRC_CELL, _SRC_NONE], detected_columns, vs_current[0], vs_current[1],
        extra_render=_date_format_picker,
    )
    ve_current = (_SRC_CELL, (file_metadata.get("validity_end") or {}).get("cell") or "") \
        if file_metadata.get("validity_end") else (_SRC_NONE, "")
    ve_src, ve_val, ve_date_fmt = _source_field(
        "Ending Date (fin de validité)", "", pending_id, "mx_validity_end",
        [_SRC_CELL, _SRC_NONE], detected_columns, ve_current[0], ve_current[1],
        extra_render=_date_format_picker,
    )
    date_fmt = date_fmt or ve_date_fmt or "parse_date_fr"

    generic_current = (
        (_SRC_COLUMN, (existing_pc.get("generic_code") or {}).get("source_col") or "")
        if existing_pc.get("generic_code") else
        (_SRC_CELL, (file_metadata.get("ramery_generic_code") or {}).get("cell") or "")
        if file_metadata.get("ramery_generic_code") else
        (_SRC_FIXED, defaults.get("article_generique") or "")
        if defaults.get("article_generique") else
        (_SRC_NONE, "")
    )
    generic_src, generic_val, _ = _source_field(
        "Article générique associé",
        "Variable selon le produit (colonne) ou fixe pour tout le fichier (cartouche / valeur).",
        pending_id, "mx_generic", [_SRC_COLUMN, _SRC_CELL, _SRC_FIXED, _SRC_NONE],
        detected_columns, generic_current[0], generic_current[1],
    )

    siren_current = (_SRC_CELL, (file_metadata.get("siren_fournisseur") or {}).get("cell") or "") \
        if file_metadata.get("siren_fournisseur") else (_SRC_NONE, "")
    siren_src, siren_val, _ = _source_field(
        "SIREN Fournisseur", "", pending_id, "mx_siren", [_SRC_CELL, _SRC_NONE],
        detected_columns, siren_current[0], siren_current[1],
    )

    st.markdown("**Minimum Quantity**")
    st.caption("Toujours une valeur fixe (identique pour toutes les lignes).")
    min_qty = st.number_input(
        "Quantité minimum", min_value=1, step=1,
        value=int(defaults.get("minimum_quantity") or 1),
        key=f"min_qty_matrix_{pending_id}", label_visibility="collapsed",
    )
    st.divider()

    with st.expander("🏷️ Code article généré (obligatoire)", expanded=True):
        attr_keys = [a.get("key") for a in (attr_out or []) if a.get("key")]
        available_vars = ["designation", *attr_keys, "variant_code", "tier_label"]
        st.caption("Variables disponibles : " + ", ".join(f"{{{v}}}" for v in available_vars))
        default_template = gery_export.get("derived_code_template") or (
            "{designation}" + "".join(f" | {{{k}}}" for k in attr_keys)
            + " | {variant_code} | {tier_label}"
        )
        derived_code_template = st.text_input(
            "Modèle du code article Gery", value=default_template,
            key=f"derived_tpl_matrix_{pending_id}",
            help="Les segments dont une variable est absente sont automatiquement omis.",
        )

    # ── Reconstruction du mapping ────────────────────────────────────────────
    def _col_index(letter: str) -> int:
        n = 0
        for ch in letter.strip().upper():
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n - 1

    def _range_str(letters: list[str]) -> str:
        if not letters:
            return "A:A"
        idxs = sorted(_col_index(letter) for letter in letters)
        return f"{_col_letter(idxs[0])}:{_col_letter(idxs[-1])}"

    new_pc: dict[str, Any] = {
        "designation": {"source_col": designation_col, "transform": "strip", "required": True},
    }
    if code_src == _SRC_COLUMN and code_val:
        new_pc["supplier_product_code"] = {"source_col": code_val, "required": True}
    elif code_src == _SRC_TEMPLATE and code_val:
        new_pc["supplier_product_code"] = {"derived_from": code_val, "required": True}

    new_defaults: dict[str, Any] = dict(defaults)
    new_file_metadata: dict[str, Any] = {}

    if generic_src == _SRC_COLUMN and generic_val:
        new_pc["generic_code"] = {"source_col": generic_val}
        new_defaults.pop("article_generique", None)
    elif generic_src == _SRC_CELL and generic_val:
        new_file_metadata["ramery_generic_code"] = {"cell": generic_val}
        new_defaults.pop("article_generique", None)
    elif generic_src == _SRC_FIXED and generic_val:
        new_defaults["article_generique"] = generic_val

    new_attributes = list(attr_out or [])
    if unit_src == _SRC_COLUMN and unit_val:
        new_attributes.append(
            {"key": "unit_of_measure", "source_col": unit_val, "data_type": "string"}
        )
        new_defaults["unit_of_measure"] = "U"
    elif unit_src == _SRC_FIXED:
        new_defaults["unit_of_measure"] = unit_val or "U"

    if vs_src == _SRC_CELL and vs_val:
        new_file_metadata["validity_start"] = {"cell": vs_val, "transform": date_fmt}
    if ve_src == _SRC_CELL and ve_val:
        new_file_metadata["validity_end"] = {"cell": ve_val, "transform": date_fmt}
    if siren_src == _SRC_CELL and siren_val:
        new_file_metadata["siren_fournisseur"] = {"cell": siren_val}

    if family_src == _SRC_COLUMN and family_val:
        new_pc["family"] = {"source_col": family_val, "transform": "strip"}
    elif family_src == _SRC_FIXED and family_val:
        new_pc["family"] = {"constant": family_val}
    if subfamily_src == _SRC_COLUMN and subfamily_val:
        new_pc["subfamily"] = {"source_col": subfamily_val, "transform": "strip"}
    elif subfamily_src == _SRC_FIXED and subfamily_val:
        new_pc["subfamily"] = {"constant": subfamily_val}

    new_groups: list[dict[str, Any]] = []
    for row in price_grid:
        letter = (row.get("Colonne") or "").strip()
        tier = (row.get("Palier") or "").strip()
        variant = (row.get("Variante") or "").strip()
        if not letter:
            continue
        if new_groups and new_groups[-1]["tier_label"] == tier:
            new_groups[-1]["columns"].append(letter)
            new_groups[-1]["variants"].append(variant)
        else:
            new_groups.append({"columns": [letter], "tier_label": tier, "variants": [variant]})

    product_letters = [v.get("source_col") for v in new_pc.values() if v.get("source_col")]
    price_letters_final = [c for g in new_groups for c in g["columns"]]

    new_row_filter: dict[str, Any] = {}
    if designation_col:
        new_row_filter["must_have_value_in"] = [designation_col]
    if price_letters_final:
        new_row_filter["must_have_value_in_any"] = price_letters_final

    new_defaults["minimum_quantity"] = int(min_qty)
    for k, v in {
        "item_purchase_type": "Catalogue", "code_tva": "TVA20", "purchase_type": "Direct",
        "gen_prod_posting_group": "", "job_cost_code": "", "tree_code": "",
        "master_code": "", "item_category_code": "", "product_group_code": "",
    }.items():
        new_defaults.setdefault(k, v)

    result: dict[str, Any] = {
        "supplier_code": supplier_code.strip(),
        "mapping_version": int(data.get("mapping_version", 1)),
        "description": data.get("description", ""),
        "upload_mode": data.get("upload_mode", "full"),
    }
    if "sharepoint_folder" in data:
        result["sharepoint_folder"] = data["sharepoint_folder"]
    if "filename_keywords" in data:
        result["filename_keywords"] = data["filename_keywords"]
    result["sheet_match"] = sheet_match
    result["header_detection"] = {"mode": "explicit", "row": int(variant_header_row)}
    result["data_starts_row"] = int(data_starts_row)
    result["extraction_mode"] = "matrix"
    if new_row_filter:
        result["row_filter"] = new_row_filter
    result["data_zone"] = {
        "rows": f"{int(data_starts_row)}:{int(data_ends_row)}",
        "product_columns": _range_str(product_letters),
        "price_matrix_columns": _range_str(price_letters_final),
    }
    result["product_columns"] = new_pc
    if new_attributes:
        result["attributes"] = new_attributes
    result["price_matrix"] = {
        "tier_axis": {
            "header_row": int(tier_header_row),
            "type": "quantity_range",
            "fallback_unit": tier_fallback_unit.strip() or "m²",
            "detect_per_block": bool(detect_per_block),
        },
        "variant_axis": {
            "header_row": int(variant_header_row),
            "dimension_name": variant_dimension_name.strip() or "variante",
        },
        "column_groups": new_groups,
        "price_type": price_type.strip() or "list",
        "currency": currency.strip() or "EUR",
        "transform": decimal_fmt,
    }
    if data.get("commercial_rules"):
        result["commercial_rules"] = data["commercial_rules"]
    if new_file_metadata:
        result["file_metadata"] = new_file_metadata
    result["gery_export"] = {
        "enabled": True,
        "flatten_strategy": "cartesian",
        "derived_code_template": derived_code_template.strip() or "{supplier_product_code}",
        "defaults": new_defaults,
        "price_export_mapping": {"direct_unit_cost": price_type.strip() or "list"},
    }
    return result


# ─── Formulaire guidé — mode matrix ─────────────────────────────────────────

def render_matrix_form(
    data: dict[str, Any], pending_id: str, columns: list[tuple[str, str]] | None = None
) -> dict[str, Any] | None:
    """Formulaire guidé pour un mapping en mode 'matrix' (prix palier × variante)."""
    render_columns_reference(columns)
    base = _render_general_info(data, pending_id)

    st.markdown("##### Zone de données")
    dz = data.get("data_zone") or {}
    c1, c2, c3 = st.columns(3)
    dz_rows = c1.text_input("Lignes (ex: 10:31)", value=dz.get("rows", "") or "",
                            key=f"dz_rows_{pending_id}")
    dz_prod = c2.text_input("Colonnes produit (ex: A:F)", value=dz.get("product_columns", "") or "",
                            key=f"dz_prod_{pending_id}")
    dz_price = c3.text_input("Colonnes matrice prix (ex: G:L)",
                             value=dz.get("price_matrix_columns", "") or "",
                             key=f"dz_price_{pending_id}")

    st.markdown("##### Colonnes produit")
    st.caption("`source_col` = lettre de colonne, OU `derived_from` = template "
               "(ex: {designation} | EP{epaisseur}).")
    pc = data.get("product_columns") or {}
    pc_rows = []
    for fname, col in pc.items():
        col = col or {}
        pc_rows.append({
            "champ": fname,
            "source_col": col.get("source_col", "") or "",
            "derived_from": col.get("derived_from", "") or "",
            "transform": transform_to_str(col.get("transform")),
            "required": bool(col.get("required", False)),
        })
    pc_df = st.data_editor(
        pc_rows, num_rows="dynamic", use_container_width=True, key=f"pc_{pending_id}",
        column_config={
            "champ": st.column_config.TextColumn("Champ (ex: designation, family)"),
            "source_col": st.column_config.TextColumn("Colonne source"),
            "derived_from": st.column_config.TextColumn("Template dérivé"),
            "transform": st.column_config.TextColumn("Transformations"),
            "required": st.column_config.CheckboxColumn("Obligatoire"),
        },
    )

    with st.expander("⚙️ Attributs techniques", expanded=False):
        attr_df_out = _render_attributes(data.get("attributes"), f"matrix_attrs_{pending_id}")

    st.markdown("##### Matrice de prix")
    pm = data.get("price_matrix") or {}
    ta = pm.get("tier_axis") or {}
    va = pm.get("variant_axis") or {}
    c4, c5 = st.columns(2)
    ta_header = c4.number_input("Ligne d'en-tête des paliers", min_value=1, step=1,
                                value=int(ta.get("header_row") or 1), key=f"ta_hr_{pending_id}")
    va_header = c5.number_input("Ligne d'en-tête des variantes", min_value=1, step=1,
                                value=int(va.get("header_row") or 1), key=f"va_hr_{pending_id}")
    c6, c7 = st.columns(2)
    ta_unit = c6.text_input("Unité des paliers (ex: m²)", value=ta.get("fallback_unit", "m²"),
                            key=f"ta_u_{pending_id}")
    va_dim = c7.text_input("Nom de la dimension variante (ex: couleur)",
                           value=va.get("dimension_name", "variante"), key=f"va_d_{pending_id}")
    ta_detect = st.checkbox(
        "Paliers détectés par bloc",
        value=bool(ta.get("detect_per_block", False)),
        key=f"ta_db_{pending_id}",
    )

    st.markdown("###### Groupes de colonnes (palier × variantes)")
    st.caption("`columns` et `variants` : valeurs séparées par des virgules. "
               "Ex: columns = G, H  |  variants = ALU, BLANC")
    cg = pm.get("column_groups") or []
    cg_rows = []
    for g in cg:
        g = g or {}
        cg_rows.append({
            "columns": ", ".join(g.get("columns", [])),
            "tier_label": g.get("tier_label", ""),
            "variants": ", ".join(g.get("variants", [])),
        })
    cg_df = st.data_editor(
        cg_rows, num_rows="dynamic", use_container_width=True, key=f"cg_{pending_id}",
        column_config={
            "columns": st.column_config.TextColumn("Colonnes (virgules)"),
            "tier_label": st.column_config.TextColumn("Palier (ex: 0-500m²)"),
            "variants": st.column_config.TextColumn("Variantes (virgules)"),
        },
    )
    c8, c9 = st.columns(2)
    pm_type = c8.text_input("Type de prix", value=pm.get("price_type", "list"),
                            key=f"pm_pt_{pending_id}")
    pm_cur = c9.text_input("Devise", value=pm.get("currency", "EUR"), key=f"pm_cur_{pending_id}")
    pm_tr_default = transform_to_str(pm.get("transform", "parse_decimal_fr")) or "parse_decimal_fr"
    pm_tr = st.text_input("Transformation prix", value=pm_tr_default, key=f"pm_tr_{pending_id}")

    with st.expander("⚙️ Validité du tarif", expanded=False):
        fm = _render_file_metadata(data, pending_id)
    gery = _render_gery_export(data, pending_id)

    if not st.button("Enregistrer (formulaire matrix)", key=f"save_matrix_{pending_id}"):
        return None

    new_pc: dict[str, Any] = {}
    for row in pc_df:
        champ = (row.get("champ") or "").strip()
        if not champ:
            continue
        entry: dict[str, Any] = {}
        sc = (row.get("source_col") or "").strip()
        derived = (row.get("derived_from") or "").strip()
        if sc:
            entry["source_col"] = sc
        elif derived:
            entry["derived_from"] = derived
        tr = transform_from_str(row.get("transform") or "")
        if tr:
            entry["transform"] = tr
        if row.get("required"):
            entry["required"] = True
        new_pc[champ] = entry

    new_cg = []
    for row in cg_df:
        cols = [c.strip() for c in (row.get("columns") or "").split(",") if c.strip()]
        variants = [v.strip() for v in (row.get("variants") or "").split(",") if v.strip()]
        if not cols:
            continue
        new_cg.append({
            "columns": cols,
            "tier_label": (row.get("tier_label") or "").strip(),
            "variants": variants,
        })

    price_matrix = {
        "tier_axis": {
            "header_row": int(ta_header),
            "type": ta.get("type", "quantity_range"),
            "fallback_unit": ta_unit.strip() or "m²",
            "detect_per_block": bool(ta_detect),
        },
        "variant_axis": {
            "header_row": int(va_header),
            "dimension_name": va_dim.strip() or "variante",
        },
        "column_groups": new_cg,
        "price_type": pm_type.strip() or "list",
        "currency": pm_cur.strip() or "EUR",
        "transform": transform_from_str(pm_tr) or "parse_decimal_fr",
    }

    result = dict(base)
    result["extraction_mode"] = "matrix"
    if "product_kind" in data:
        result["product_kind"] = data["product_kind"]
    result["data_zone"] = {
        "rows": dz_rows.strip(),
        "product_columns": dz_prod.strip(),
        "price_matrix_columns": dz_price.strip(),
    }
    result["product_columns"] = new_pc
    if attr_df_out:
        result["attributes"] = attr_df_out
    result["price_matrix"] = price_matrix
    if data.get("commercial_rules"):  # règles commerciales conservées telles quelles
        result["commercial_rules"] = data["commercial_rules"]
    if fm:
        result["file_metadata"] = fm
    result["gery_export"] = gery
    return result


def _snake(text: str) -> str:
    """Texte libre → identifiant snake_case ASCII, pour suggérer un nom de variable."""
    s = re.sub(r"[^0-9a-zA-Z]+", "_", (text or "").strip().lower()).strip("_")
    return s or "valeur"


# ─── Formulaire simplifié v2 — orienté métier, sans YAML (mode multi_table) ─

def render_multi_table_form_simple(
    data: dict[str, Any],
    pending_id: str,
    preview_text: str,
    sheets: list[str],
    supplier_guess: str = "",
) -> dict[str, Any] | None:
    """Formulaire orienté métier pour le mode 'multi_table' (plusieurs tableaux, ex.
    Agenor). Même esprit que les autres formulaires simplifiés : dropdowns sur colonnes
    détectées, pas de lettre à deviner, aperçu recalculé en direct.
    """
    st.caption(
        "Pour les fichiers avec plusieurs tableaux distincts dans la même feuille (ex. "
        "prestations Agenor). Un tableau = une section ci-dessous. L'aperçu à droite se "
        "met à jour automatiquement."
    )

    file_metadata = data.get("file_metadata") or {}
    gery_export = data.get("gery_export") or {}
    defaults = gery_export.get("defaults") or {}
    existing_tables = data.get("tables") or []

    st.markdown("##### 📍 Informations générales")
    c1, c2 = st.columns(2)
    current_sheet = data.get("sheet_match") if isinstance(data.get("sheet_match"), str) else None
    sheet_options = sheets or ([current_sheet] if current_sheet else [])
    if sheet_options:
        idx = sheet_options.index(current_sheet) if current_sheet in sheet_options else 0
        sheet_match = c1.selectbox(
            "Feuille Excel", sheet_options, index=idx, key=f"sheet_mt_{pending_id}"
        )
    else:
        sheet_match = c1.text_input(
            "Feuille Excel", value=current_sheet or "", key=f"sheet_mt_{pending_id}"
        )
    supplier_code = c2.text_input(
        "Code fournisseur *(obligatoire)*",
        value=data.get("supplier_code") or supplier_guess or "",
        key=f"supplier_code_mt_{pending_id}",
    )
    if not supplier_code.strip():
        st.warning(
            "⚠️ Code fournisseur vide — les exports ne seront pas correctement nommés/rangés."
        )

    st.markdown("**Prix — réglages communs à tous les tableaux**")
    c3, c4, c5 = st.columns(3)
    price_type = c3.text_input(
        "Type de prix (ex: forfait)", value=(gery_export.get("price_export_mapping") or {}).get(
            "direct_unit_cost", "forfait"
        ),
        key=f"price_type_mt_{pending_id}",
    )

    def _decimal_format_picker(container):
        options = {
            "Virgule française (1234,56)": "parse_decimal_fr",
            "Point US (1234.56)": "parse_decimal_us",
        }
        labels = list(options)
        current_fmt = (
            (existing_tables[0].get("prices") or [{}])[0].get("transform")
            if existing_tables else None
        ) or "parse_decimal_fr"
        default_label = next((lbl for lbl, v in options.items() if v == current_fmt), labels[0])
        chosen = container.selectbox(
            "Format du nombre", labels, index=labels.index(default_label),
            key=f"decimalfmt_mt_{pending_id}",
        )
        return options[chosen]

    decimal_fmt = _decimal_format_picker(c4)
    currency = c5.text_input("Devise", value="EUR", key=f"currency_mt_{pending_id}")

    st.divider()
    st.markdown("##### 🧾 Tableaux")
    nb_tables = st.number_input(
        "Nombre de tableaux dans le fichier", min_value=1, step=1,
        value=max(1, len(existing_tables)), key=f"nb_tables_mt_{pending_id}",
    )

    layouts = {
        "Grille (plusieurs colonnes de prix par dimension — ex: fréquence, taille…)": "matrix_2D",
        "Liste simple (une seule colonne de prix)": "barème_1D",
    }
    layout_labels = list(layouts)

    def _col_index(letter: str) -> int:
        n = 0
        for ch in letter.strip().upper():
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n - 1

    def _range_str(letters: list[str]) -> str:
        letters = [letter for letter in letters if letter]
        if not letters:
            return "A:A"
        idxs = sorted(_col_index(letter) for letter in letters)
        return f"{_col_letter(idxs[0])}:{_col_letter(idxs[-1])}"

    new_tables: list[dict[str, Any]] = []
    for i in range(int(nb_tables)):
        t = existing_tables[i] if i < len(existing_tables) else {}
        tpl = t.get("product_template") or {}
        zone = t.get("zone") or {}
        existing_dims = t.get("col_dimensions") or []
        default_layout_key = next(
            (lbl for lbl, v in layouts.items() if v == t.get("layout")), layout_labels[0]
        )
        with st.expander(f"Tableau {i + 1} : {t.get('name') or '(nouveau)'}", expanded=(i == 0)):
            tc1, tc2 = st.columns(2)
            name = tc1.text_input(
                "Nom technique (snake_case)", value=t.get("name") or f"tableau_{i + 1}",
                key=f"mt_name_{pending_id}_{i}",
            )
            description = tc2.text_input(
                "Description", value=t.get("description") or "", key=f"mt_desc_{pending_id}_{i}",
            )
            layout_label = st.selectbox(
                "Type de tableau", layout_labels,
                index=layout_labels.index(default_layout_key), key=f"mt_layout_{pending_id}_{i}",
            )
            layout = layouts[layout_label]

            zc1, zc2, zc3 = st.columns(3)
            header_row = zc1.number_input(
                "Ligne d'en-tête", min_value=1, step=1,
                value=int(zone.get("header_row") or 1), key=f"mt_hr_{pending_id}_{i}",
            )
            _existing_dr = (zone.get("data_rows") or "").split(":")
            _dr_start = int(_existing_dr[0]) if len(_existing_dr) == 2 and _existing_dr[0].strip().isdigit() else None
            _dr_end = int(_existing_dr[1]) if len(_existing_dr) == 2 and _existing_dr[1].strip().isdigit() else None
            data_row_start = zc2.number_input(
                "Première ligne de données", min_value=1, step=1,
                value=int(_dr_start or (int(header_row) + 1)), key=f"mt_drs_{pending_id}_{i}",
            )
            data_row_end = zc3.number_input(
                "Dernière ligne de données", min_value=int(data_row_start), step=1,
                value=max(int(data_row_start), int(_dr_end or (int(data_row_start) + 10))),
                key=f"mt_dre_{pending_id}_{i}",
            )

            table_columns = parse_detected_columns(preview_text, int(header_row))
            row_labels, row_by_label = _column_choices(table_columns)
            existing_row_col = (zone.get("cols") or "").split(":")[0].strip() if zone.get("cols") else ""
            row_default_idx = next(
                (idx for idx, lbl in enumerate(row_labels) if row_by_label[lbl] == existing_row_col), 0
            ) if row_labels else 0
            row_col_label = st.selectbox(
                "Colonne indicatrice de ligne (identifie chaque ligne du tableau)",
                row_labels, index=row_default_idx if row_labels else 0,
                key=f"mt_rowcol_{pending_id}_{i}",
                help="Ex: la taille de la base vie, le nom de la prestation… Sert aussi de "
                     "valeur de repli pour les variables du modèle ci-dessous.",
            ) if row_labels else st.text_input(
                "Colonne indicatrice de ligne (lettre)", value=existing_row_col,
                key=f"mt_rowcol_txt_{pending_id}_{i}",
            )
            row_col = row_by_label[row_col_label] if row_labels else row_col_label
            row_var_name = _snake(dict(table_columns).get(row_col, "") or row_col)

            fc1, fc2 = st.columns(2)
            family = fc1.text_input("Famille", value=tpl.get("family") or "",
                                    key=f"mt_family_{pending_id}_{i}")
            subfamily = fc2.text_input("Sous-famille", value=tpl.get("subfamily") or "",
                                       key=f"mt_subfamily_{pending_id}_{i}")

            new_dims: list[dict[str, Any]] = []
            dimension_key = ""
            price_col_simple = ""
            if layout == "matrix_2D":
                dimension_key = st.text_input(
                    "Nom de la dimension (ex: frequency, taille…)",
                    value=(existing_dims[0].get("key") if existing_dims else "") or "dimension",
                    key=f"mt_dimkey_{pending_id}_{i}",
                )
                st.caption(
                    "Une ligne par valeur de dimension (ex: 1x/semaine, 2x/semaine…). "
                    "« Colonne temps max » optionnelle."
                )
                col_letters = [ltr for ltr, _ in table_columns] or [""]
                dim_rows = [
                    {
                        "Valeur": d.get("value", ""),
                        "Colonne prix": d.get("price_col", "") if d.get("price_col") in col_letters
                        else (col_letters[0] if col_letters else ""),
                        "Colonne temps max": d.get("max_time_col") or "",
                    }
                    for d in existing_dims
                ]
                dim_df = st.data_editor(
                    dim_rows, num_rows="dynamic", use_container_width=True,
                    key=f"mt_dims_{pending_id}_{i}",
                    column_config={
                        "Valeur": st.column_config.TextColumn("Valeur (ex: 1x_semaine)"),
                        "Colonne prix": st.column_config.SelectboxColumn(
                            "Colonne prix", options=col_letters
                        ),
                        "Colonne temps max": st.column_config.SelectboxColumn(
                            "Colonne temps max (optionnel)", options=[""] + col_letters
                        ),
                    },
                )
                for row in dim_df:
                    value = (row.get("Valeur") or "").strip()
                    price_col = (row.get("Colonne prix") or "").strip()
                    if not value or not price_col:
                        continue
                    max_time_col = (row.get("Colonne temps max") or "").strip()
                    cols = [price_col] + ([max_time_col] if max_time_col else [])
                    dim: dict[str, Any] = {
                        "columns": cols, "key": dimension_key.strip() or "dimension",
                        "value": value, "price_col": price_col,
                    }
                    if max_time_col:
                        dim["max_time_col"] = max_time_col
                    new_dims.append(dim)
                price_col_simple = new_dims[0]["price_col"] if new_dims else (col_letters[0] if col_letters else "")
            else:
                _, price_col_simple, _ = _source_field(
                    "Colonne prix *(obligatoire)*", "",
                    pending_id, f"mt_price_{i}", [_SRC_COLUMN], table_columns,
                    _SRC_COLUMN,
                    (t.get("prices") or [{}])[0].get("source_col", "") if t.get("prices") else "",
                )

            st.markdown("**Attributs techniques** *(optionnel — ex: temps max en durée)*")
            attrs_out = _render_attributes_dropdown(
                t.get("attributes"), f"mt_attrs_{pending_id}_{i}", table_columns
            )

            with st.expander("🏷️ Modèle du produit (désignation + code article)", expanded=True):
                st.caption(
                    f"Variable réelle disponible : `{{{dimension_key or 'dimension'}}}` "
                    f"(valeur de la dimension). Toute AUTRE variable (ex: `{{{row_var_name}}}`) "
                    "prend automatiquement la valeur de la colonne indicatrice de ligne — "
                    "le nom entre accolades est libre, seule la position compte."
                )
                default_desig = tpl.get("designation_template") or (
                    f"{{{row_var_name}}} — {{{dimension_key or 'dimension'}}}"
                    if layout == "matrix_2D" else f"{{{row_var_name}}}"
                )
                designation_template = st.text_input(
                    "Modèle de désignation", value=default_desig,
                    key=f"mt_desigtpl_{pending_id}_{i}",
                )
                default_code = tpl.get("supplier_product_code_template") or (
                    f"{_snake(supplier_code).upper()}-{{{row_var_name}_slug}}-{{{dimension_key or 'dimension'}_slug}}"
                    if layout == "matrix_2D" else f"{_snake(supplier_code).upper()}-{{{row_var_name}_slug}}"
                )
                code_template = st.text_input(
                    "Modèle de code article", value=default_code,
                    key=f"mt_codetpl_{pending_id}_{i}",
                )

        tbl: dict[str, Any] = {
            "name": name.strip() or f"tableau_{i + 1}",
            "zone": {
                "header_row": int(header_row),
                "data_rows": f"{int(data_row_start)}:{int(data_row_end)}",
                "cols": _range_str(
                    [row_col]
                    + [d["price_col"] for d in new_dims]
                    + [d["max_time_col"] for d in new_dims if d.get("max_time_col")]
                    + ([price_col_simple] if price_col_simple else [])
                    + [a["source_col"] for a in attrs_out]
                ),
            },
            "layout": layout,
        }
        if description.strip():
            tbl["description"] = description.strip()
        if new_dims:
            tbl["col_dimensions"] = new_dims
        ptpl: dict[str, Any] = {
            "designation_template": designation_template.strip() or f"{{{row_var_name}}}",
            "supplier_product_code_template": code_template.strip() or f"{{{row_var_name}_slug}}",
        }
        if family.strip():
            ptpl["family"] = family.strip()
        if subfamily.strip():
            ptpl["subfamily"] = subfamily.strip()
        tbl["product_template"] = ptpl
        if price_col_simple:
            tbl["prices"] = [{
                "type": price_type.strip() or "forfait", "source_col": price_col_simple,
                "transform": decimal_fmt, "currency": currency.strip() or "EUR",
            }]
        if attrs_out:
            tbl["attributes"] = attrs_out
        new_tables.append(tbl)

    st.divider()
    st.markdown("##### 📅 Validité, code générique, SIREN")
    c6, c7 = st.columns(2)
    vs_cell = c6.text_input(
        "Cellule début de validité (optionnel)",
        value=(file_metadata.get("validity_start") or {}).get("cell", "") or "",
        key=f"mt_vs_cell_{pending_id}",
    )
    ve_cell = c7.text_input(
        "Cellule fin de validité (optionnel)",
        value=(file_metadata.get("validity_end") or {}).get("cell", "") or "",
        key=f"mt_ve_cell_{pending_id}",
    )
    date_options = {"JJ/MM/AAAA": "parse_date_fr", "AAAA-MM-JJ": "parse_date_iso"}
    date_labels = list(date_options)
    _current_date_fmt = (
        (file_metadata.get("validity_start") or {}).get("transform")
        or (file_metadata.get("validity_end") or {}).get("transform")
        or "parse_date_fr"
    )
    date_default_label = next((lbl for lbl, v in date_options.items() if v == _current_date_fmt), date_labels[0])
    date_fmt = date_options[st.selectbox(
        "Format de date", date_labels, index=date_labels.index(date_default_label),
        key=f"mt_datefmt_{pending_id}",
    )]
    st.caption(
        "Pour une date unique combinée (ex: « Validité du 01/01 au 31/12 »), utilise "
        "l'onglet « Formulaire avancé » ou « YAML » (regex `validity_period`)."
    )

    generic_cell = st.text_input(
        "Cellule du code article générique Ramery (optionnel)",
        value=(file_metadata.get("ramery_generic_code") or {}).get("cell", "") or "",
        key=f"mt_generic_cell_{pending_id}",
    )
    siren_cell = st.text_input(
        "Cellule SIREN fournisseur (optionnel)",
        value=(file_metadata.get("siren_fournisseur") or {}).get("cell", "") or "",
        key=f"mt_siren_cell_{pending_id}",
    )

    c8, c9 = st.columns(2)
    unit_of_measure = c8.text_input(
        "Unité (ex: FORFAIT, U…)", value=defaults.get("unit_of_measure") or "FORFAIT",
        key=f"mt_unit_{pending_id}",
    )
    min_qty = c9.number_input(
        "Quantité minimum", min_value=1, step=1,
        value=int(defaults.get("minimum_quantity") or 1), key=f"mt_minqty_{pending_id}",
    )

    # ── Reconstruction du mapping ────────────────────────────────────────────
    new_file_metadata: dict[str, Any] = {}
    if vs_cell.strip():
        new_file_metadata["validity_start"] = {"cell": vs_cell.strip(), "transform": date_fmt}
    if ve_cell.strip():
        new_file_metadata["validity_end"] = {"cell": ve_cell.strip(), "transform": date_fmt}
    if generic_cell.strip():
        new_file_metadata["ramery_generic_code"] = {"cell": generic_cell.strip()}
    if siren_cell.strip():
        new_file_metadata["siren_fournisseur"] = {"cell": siren_cell.strip()}

    new_defaults: dict[str, Any] = dict(defaults)
    new_defaults["unit_of_measure"] = unit_of_measure.strip() or "FORFAIT"
    new_defaults["minimum_quantity"] = int(min_qty)
    for k, v in {"item_purchase_type": "Catalogue", "code_tva": "TVA20"}.items():
        new_defaults.setdefault(k, v)
    if not generic_cell.strip() and defaults.get("article_generique"):
        new_defaults["article_generique"] = defaults["article_generique"]

    result: dict[str, Any] = {
        "supplier_code": supplier_code.strip(),
        "mapping_version": int(data.get("mapping_version", 1)),
        "description": data.get("description", ""),
        "upload_mode": data.get("upload_mode", "full"),
    }
    if "sharepoint_folder" in data:
        result["sharepoint_folder"] = data["sharepoint_folder"]
    if "filename_keywords" in data:
        result["filename_keywords"] = data["filename_keywords"]
    result["sheet_match"] = sheet_match
    first_zone = new_tables[0]["zone"] if new_tables else {}
    result["header_detection"] = {
        "mode": "explicit", "row": int(first_zone.get("header_row") or 1),
    }
    result["data_starts_row"] = int((first_zone.get("data_rows") or "2:2").split(":")[0])
    result["extraction_mode"] = "multi_table"
    result["product_kind"] = data.get("product_kind") or "service"
    result["tables"] = new_tables
    if new_file_metadata:
        result["file_metadata"] = new_file_metadata
    result["gery_export"] = {
        "enabled": True,
        "flatten_strategy": "cartesian",
        "defaults": new_defaults,
        "price_export_mapping": {"direct_unit_cost": price_type.strip() or "forfait"},
    }
    return result


# ─── Formulaire guidé — mode multi_table ────────────────────────────────────

def render_multi_table_form(
    data: dict[str, Any], pending_id: str, columns: list[tuple[str, str]] | None = None
) -> dict[str, Any] | None:
    """Formulaire guidé pour un mapping en mode 'multi_table' (plusieurs tableaux)."""
    render_columns_reference(columns)
    base = _render_general_info(data, pending_id)

    kinds = ["service", "physical"]
    pk = data.get("product_kind", "service")
    product_kind = st.selectbox("Type de produit (product_kind)", kinds,
                                index=kinds.index(pk) if pk in kinds else 0, key=f"pk_{pending_id}")

    tables = data.get("tables") or []
    st.markdown("##### Tableaux")
    nb = st.number_input("Nombre de tableaux", min_value=1, step=1,
                         value=max(1, len(tables)), key=f"nt_{pending_id}")

    new_tables: list[dict[str, Any]] = []
    for i in range(int(nb)):
        t = tables[i] if i < len(tables) else {}
        with st.expander(f"Tableau {i + 1} : {t.get('name', '(nouveau)')}", expanded=(i == 0)):
            name = st.text_input("Nom", value=t.get("name", ""), key=f"t_name_{pending_id}_{i}")
            desc = st.text_input("Description", value=t.get("description", "") or "",
                                 key=f"t_desc_{pending_id}_{i}")
            zone = t.get("zone") or {}
            z1, z2, z3 = st.columns(3)
            z_hr = z1.number_input("Ligne d'en-tête", min_value=1, step=1,
                                   value=int(zone.get("header_row") or 1),
                                   key=f"t_zhr_{pending_id}_{i}")
            z_dr = z2.text_input("Lignes de données (ex: 9:18)",
                                 value=zone.get("data_rows", "") or "",
                                 key=f"t_zdr_{pending_id}_{i}")
            z_cols = z3.text_input("Colonnes (ex: A:G)", value=zone.get("cols", "") or "",
                                   key=f"t_zc_{pending_id}_{i}")
            layout = st.text_input("Layout (ex: matrix_2D, barème_1D)",
                                   value=t.get("layout", "") or "",
                                   key=f"t_lay_{pending_id}_{i}")

            st.caption("Template produit")
            pt = t.get("product_template") or {}
            p1, p2 = st.columns(2)
            pt_desig = p1.text_input("designation_template",
                                     value=pt.get("designation_template", "") or "",
                                     key=f"t_ptd_{pending_id}_{i}")
            pt_code = p2.text_input("supplier_product_code_template",
                                    value=pt.get("supplier_product_code_template", "") or "",
                                    key=f"t_ptc_{pending_id}_{i}")
            p3, p4 = st.columns(2)
            pt_fam = p3.text_input("family", value=pt.get("family", "") or "",
                                   key=f"t_ptf_{pending_id}_{i}")
            pt_sub = p4.text_input("subfamily", value=pt.get("subfamily", "") or "",
                                   key=f"t_pts_{pending_id}_{i}")

            st.caption("col_dimensions (`columns` séparées par virgules)")
            cd = t.get("col_dimensions") or []
            cd_rows = []
            for d in cd:
                d = d or {}
                cd_rows.append({
                    "columns": ", ".join(d.get("columns", [])),
                    "key": d.get("key", ""),
                    "value": d.get("value", ""),
                    "price_col": d.get("price_col", ""),
                    "max_time_col": d.get("max_time_col", "") or "",
                })
            cd_df = st.data_editor(
                cd_rows, num_rows="dynamic", use_container_width=True, key=f"t_cd_{pending_id}_{i}",
                column_config={
                    "columns": st.column_config.TextColumn("Colonnes (virgules)"),
                    "key": st.column_config.TextColumn("Clé dimension"),
                    "value": st.column_config.TextColumn("Valeur"),
                    "price_col": st.column_config.TextColumn("Colonne prix"),
                    "max_time_col": st.column_config.TextColumn("Colonne temps max"),
                },
            )

            st.caption("Prix")
            pr = t.get("prices") or []
            pr_rows = []
            for p in pr:
                p = p or {}
                pr_rows.append({
                    "type": p.get("type", ""),
                    "source_col": p.get("source_col", ""),
                    "transform": transform_to_str(p.get("transform", "parse_decimal_fr")),
                    "currency": p.get("currency", "EUR"),
                })
            pr_df = st.data_editor(
                pr_rows, num_rows="dynamic", use_container_width=True, key=f"t_pr_{pending_id}_{i}",
                column_config={
                    "type": st.column_config.TextColumn("Type (ex: forfait)"),
                    "source_col": st.column_config.TextColumn("Colonne source"),
                    "transform": st.column_config.TextColumn("Transformation"),
                    "currency": st.column_config.TextColumn("Devise"),
                },
            )

            attrs_out = _render_attributes(t.get("attributes"), f"t_attrs_{pending_id}_{i}")

        # Reconstruction du tableau (hors expander, dans la boucle)
        tbl: dict[str, Any] = {
            "name": name.strip(),
            "zone": {"header_row": int(z_hr), "data_rows": z_dr.strip(), "cols": z_cols.strip()},
            "layout": layout.strip(),
        }
        if desc.strip():
            tbl["description"] = desc.strip()
        new_cd = []
        for row in cd_df:
            cols = [c.strip() for c in (row.get("columns") or "").split(",") if c.strip()]
            if not cols:
                continue
            dim: dict[str, Any] = {
                "columns": cols,
                "key": (row.get("key") or "").strip(),
                "value": (row.get("value") or "").strip(),
                "price_col": (row.get("price_col") or "").strip(),
            }
            mtc = (row.get("max_time_col") or "").strip()
            if mtc:
                dim["max_time_col"] = mtc
            new_cd.append(dim)
        if new_cd:
            tbl["col_dimensions"] = new_cd
        if pt_desig.strip() or pt_code.strip():
            ptpl: dict[str, Any] = {
                "designation_template": pt_desig.strip(),
                "supplier_product_code_template": pt_code.strip(),
            }
            if pt_fam.strip():
                ptpl["family"] = pt_fam.strip()
            if pt_sub.strip():
                ptpl["subfamily"] = pt_sub.strip()
            tbl["product_template"] = ptpl
        new_pr = []
        for row in pr_df:
            ptype = (row.get("type") or "").strip()
            sc = (row.get("source_col") or "").strip()
            if not ptype or not sc:
                continue
            new_pr.append({
                "type": ptype, "source_col": sc,
                "transform": transform_from_str(row.get("transform") or "") or "parse_decimal_fr",
                "currency": (row.get("currency") or "EUR").strip() or "EUR",
            })
        if new_pr:
            tbl["prices"] = new_pr
        if attrs_out:
            tbl["attributes"] = attrs_out
        if t.get("row_dimension"):  # conservé tel quel (peu fréquent)
            tbl["row_dimension"] = t["row_dimension"]
        new_tables.append(tbl)

    with st.expander("⚙️ Validité du tarif", expanded=False):
        fm = _render_file_metadata(data, pending_id)
    gery = _render_gery_export(data, pending_id)

    if not st.button("Enregistrer (formulaire multi_table)", key=f"save_mt_{pending_id}"):
        return None

    result = dict(base)
    result["extraction_mode"] = "multi_table"
    result["product_kind"] = product_kind
    result["tables"] = new_tables
    if fm:
        result["file_metadata"] = fm
    result["gery_export"] = gery
    return result


# Dispatch des formulaires guidés par mode d'extraction (extensible : ajouter un
# mode = ajouter une fonction ici, sans toucher au reste).
FORM_RENDERERS = {
    "table": render_table_form,
    "matrix": render_matrix_form,
    "multi_table": render_multi_table_form,
}


# ─── Application ────────────────────────────────────────────────────────────

render_header()

vue = st.sidebar.radio("Vue", ["Validation des mappings", "Exports Gery", "❓ Aide"])
if vue == "Exports Gery":
    render_exports_view()
    st.stop()
if vue == "❓ Aide":
    render_help_view()
    st.stop()

st.subheader("Validation des mappings fournisseurs générés par IA")

if "last_action_html" in st.session_state:
    show_action_result(st.session_state.pop("last_action_html"))

try:
    pending_items = fetch_pending_list()
except Exception as exc:
    st.error(f"Impossible de contacter l'API ({API_URL}) : {exc}")
    st.stop()

# Chaque statut a sa propre liste (pas de mélange). Compteurs pour s'y retrouver.
counts = {"pending": 0, "approved": 0, "rejected": 0}
for it in pending_items:
    counts[it["status"]] = counts.get(it["status"], 0) + 1

statut_labels = {
    "pending": f"En attente ({counts['pending']})",
    "approved": f"Validés ({counts['approved']})",
    "rejected": f"Rejetés ({counts['rejected']})",
}
statut = st.sidebar.radio(
    "Statut",
    options=["pending", "approved", "rejected"],
    format_func=lambda s: statut_labels[s],
    key="statut_filter",
)

filtered = [item for item in pending_items if item["status"] == statut]

if not filtered:
    libelle = {"pending": "en attente", "approved": "validée", "rejected": "rejetée"}[statut]
    st.info(f"Aucune demande {libelle} pour le moment.")
    st.stop()

selected_idx = st.sidebar.radio(
    "Demandes",
    options=range(len(filtered)),
    format_func=lambda i: "{}{}\n{}".format(
        "🆘 " if filtered[i].get("escalated") else "",
        filtered[i]["supplier_guess"].replace("_", " ").title(),
        filtered[i]["filename"][:32] + "…" if len(filtered[i]["filename"]) > 32 else filtered[i]["filename"],
    ),
    key=f"demande_{statut}",
)
pending_id = filtered[selected_idx]["id"]

meta = fetch_detail(pending_id)

st.subheader(meta["filename"])
c1, c2, c3, c4 = st.columns(4)
c1.metric("Fournisseur deviné", meta["supplier_guess"])
c2.metric("Dossier SharePoint", meta["folder_name"])
c3.markdown(
    f'<div style="font-size:13px;color:#4A4A49;margin-bottom:6px">Statut</div>'
    f'{status_badge(meta["status"])}',
    unsafe_allow_html=True,
)
c4.metric("Créé le", meta["created_at"][:19].replace("T", " "))

# ── Stepper de la pipeline ───────────────────────────────────────────────────
_yaml_mode = load_yaml(meta.get("yaml_proposed") or "").get("extraction_mode")
_confidence_val = meta.get("confidence")
_step1_done = bool((meta.get("yaml_proposed") or "").strip())
_step3_done = meta["status"] == "approved"


def _step_html(label: str, done: bool, current: bool) -> str:
    icon = "✅" if done else ("🔵" if current else "⚪")
    style = "font-weight:600;" if current else "color:#999;"
    return f'<span style="{style}">{icon} {label}</span>'


st.markdown(
    "&nbsp;➜&nbsp;".join([
        _step_html("① Suggestion IA", _step1_done, not _step1_done),
        _step_html("② Validation métier", _step3_done, _step1_done and not _step3_done),
        _step_html("③ Export généré", _step3_done, False),
    ]),
    unsafe_allow_html=True,
)

_gauge_col, _diff_col, _esc_col = st.columns([2, 1, 3])
with _gauge_col:
    if _confidence_val is not None:
        st.caption(f"💡 Confiance de la suggestion : {_confidence_val}%")
        st.progress(max(0, min(100, int(_confidence_val))) / 100)
with _diff_col:
    if _yaml_mode:
        _is_complicated = _yaml_mode in ("matrix", "multi_table") or (
            _confidence_val is not None and _confidence_val < 70
        )
        st.caption("🟠 Compliqué" if _is_complicated else "🟢 Simple")
with _esc_col:
    _escalated = bool(meta.get("escalated"))
    _esc_label = "✅ Retirer de la file support" if _escalated else "🆘 Demander l'aide du support"
    if st.button(_esc_label, key=f"escalate_btn_{pending_id}"):
        api_post(f"/api/v1/review/{pending_id}/escalate", {"escalated": not _escalated})
        st.rerun()
    if _escalated:
        st.caption("🆘 Cette demande est signalée au support.")

sheets_key = f"sheets_list_{pending_id}"
if sheets_key not in st.session_state:
    st.session_state[sheets_key] = fetch_sheets(pending_id)
sheets_list = st.session_state[sheets_key]

# Le sélecteur de feuille du formulaire simplifié (clé sheet_simple_{pending_id}) peut
# avoir déjà une valeur en session_state d'une interaction précédente — on l'utilise ici
# pour que la grille d'aperçu à droite reflète bien la feuille choisie dans le formulaire.
preview_text = fetch_preview(pending_id, sheet=st.session_state.get(f"sheet_simple_{pending_id}"))

yaml_key = f"yaml_text_{pending_id}"
yaml_content_key = f"yaml_content_{pending_id}"
if yaml_key not in st.session_state:
    st.session_state[yaml_key] = meta["yaml_proposed"]
if yaml_content_key not in st.session_state:
    st.session_state[yaml_content_key] = meta["yaml_proposed"]

# Streamlit interdit d'écrire dans st.session_state[yaml_key] une fois le widget
# text_area (onglet YAML) instancié dans le même run — on passe donc par une clé de
# "staging" (voir set_yaml_text ci-dessous) appliquée ici, avant toute instanciation.
_staged_key = f"staged_yaml_{pending_id}"
if _staged_key in st.session_state:
    _staged_yaml = st.session_state.pop(_staged_key)
    st.session_state[yaml_key] = _staged_yaml
    st.session_state[yaml_content_key] = _staged_yaml


def set_yaml_text(new_text: str) -> None:
    """Programme yaml_key/yaml_content_key pour la prochaine exécution puis relance.

    À utiliser à la place d'une écriture directe dans st.session_state[yaml_key] :
    le widget text_area de l'onglet YAML est déjà instancié à ce stade du script,
    donc Streamlit refuse toute écriture directe sur cette clé.
    """
    st.session_state[_staged_key] = new_text
    st.rerun()

# Écran partagé : édition à gauche, aperçu Excel (grille A,B,C + lignes) à droite.
col_edit, col_preview = st.columns([3, 2])

with col_preview:
    st.markdown("##### 📄 Aperçu du fichier")
    render_excel_grid(preview_text)
    web_url = meta.get("web_url")
    if web_url:
        st.link_button("📂 Ouvrir le fichier dans SharePoint", web_url, use_container_width=True)
    else:
        st.caption("Le lien d'ouverture SharePoint apparaît pour les fichiers détectés "
                   "par le watcher (nouvelles demandes).")

    st.divider()
    st.markdown("##### 📤 Aperçu export Gery")

    _preview_key = f"gery_preview_{pending_id}"
    _cached = st.session_state.get(_preview_key, {})
    _cur_yaml = st.session_state.get(yaml_content_key, "")
    _force = st.session_state.pop(f"force_recalc_{pending_id}", False)

    if st.button("🔄 Recalculer l'aperçu", key=f"recalc_preview_{pending_id}"):
        _force = True

    if _cur_yaml.strip() and (_force or _cached.get("_yaml") != _cur_yaml):
        with st.spinner("Calcul de l'aperçu…"):
            _pr = api_post(
                f"/api/v1/review/{pending_id}/export-preview",
                {"yaml_content": _cur_yaml},
            )
        if _pr.status_code == 200:
            _pd = _pr.json()
            _pd["_yaml"] = _cur_yaml
            st.session_state[_preview_key] = _pd
            _cached = _pd
        elif _pr.status_code == 422:
            st.warning("YAML invalide — corrigez le mapping pour voir l'aperçu.")
            _cached = {}
        else:
            st.warning(f"Aperçu indisponible ({_pr.status_code}).")
            _cached = {}

    if _cached:
        # Bloc métadonnées cartouche — toujours affiché si dispo
        _fm = _cached.get("file_metadata") or {}
        if _fm:
            _meta_parts = []
            _rows_have_generic_code = any(
                r.get("Article générique associé") for r in _cached.get("rows", [])
            )
            if _fm.get("ramery_generic_code"):
                _meta_parts.append(f"**Code générique Ramery :** `{_fm['ramery_generic_code']}`")
            elif _rows_have_generic_code:
                _meta_parts.append("**Code générique Ramery :** _variable par produit (colonne dédiée — voir aperçu export ci-dessous)_")
            else:
                _meta_parts.append("**Code générique Ramery :** ⚠️ *non trouvé — vérifiez `columns.generic_code` ou `file_metadata.ramery_generic_code`*")
            if _fm.get("siren_fournisseur"):
                _meta_parts.append(f"**SIREN Fournisseur :** `{_fm['siren_fournisseur']}`")
            if _fm.get("validity_start"):
                _meta_parts.append(f"**Validité :** {_fm['validity_start']} → {_fm.get('validity_end', '?')}")
            if _fm.get("contract_reference"):
                _meta_parts.append(f"**Réf. contrat :** {_fm['contract_reference']}")
            st.info("  ·  ".join(_meta_parts))

        if not _cached.get("export_enabled"):
            st.info("Export Gery désactivé pour ce fournisseur.")
        elif _cached.get("line_count", 0) == 0:
            st.warning(
                f"{_cached.get('products_parsed', 0)} produit(s) lus, 0 ligne générée. "
                "Vérifiez les colonnes."
            )
        else:
            st.caption(f"{_cached['line_count']} ligne(s) · {_cached['products_parsed']} produit(s)")
            st.dataframe(_cached["rows"], use_container_width=True, hide_index=True)
    elif not _cur_yaml.strip():
        st.caption("💡 Rédigez ou générez un YAML pour voir l'aperçu.")

with col_edit:
    tab_yaml, tab_simple, tab_form, tab_ai = st.tabs(
        ["YAML", "🧩 Formulaire simplifié", "🛠️ Formulaire avancé", "🤖 Assistant IA"]
    )

with tab_yaml:
    # ── Charger un YAML existant du même dossier ──────────────────────────
    _folder_key = meta["folder_name"].lower()
    try:
        _fm_resp = api_get("/api/v1/suppliers/folder-mapping")
        _folder_yamls = _fm_resp.json().get(_folder_key, []) if _fm_resp.status_code == 200 else []
    except Exception:
        _folder_yamls = []

    if _folder_yamls:
        _yaml_options = ["— partir de zéro —"] + [e["supplier_code"] for e in _folder_yamls]
        _chosen = st.selectbox(
            "📂 Charger un YAML existant du dossier comme base",
            _yaml_options,
            key=f"yaml_loader_{pending_id}",
            help="Utile quand un autre fichier du même fournisseur a déjà un mapping validé.",
        )
        if _chosen != _yaml_options[0]:
            if st.button("Charger ce YAML", key=f"load_yaml_btn_{pending_id}", type="primary"):
                try:
                    _yr = api_get(f"/api/v1/suppliers/{_chosen}/yaml")
                    if _yr.status_code == 200:
                        _loaded = _yr.json()["yaml_content"]
                        set_yaml_text(_loaded)
                    else:
                        st.error(f"Impossible de charger le YAML ({_yr.status_code}).")
                except Exception as _exc:
                    st.error(f"Erreur : {_exc}")

    # ── Générer le YAML avec l'IA (à la demande) ─────────────────────────
    if f"ai_confidence_{pending_id}" not in st.session_state:
        st.session_state[f"ai_confidence_{pending_id}"] = meta.get("confidence")

    if st.button(
        "🤖 Générer avec l'IA",
        key=f"gen_ai_{pending_id}",
        help="Analyse le fichier et génère un mapping YAML automatiquement (appel IA "
             "réel à chaque clic).",
    ):
        with st.spinner("L'IA analyse le fichier…"):
            _gen_resp = api_get(f"/api/v1/review/{pending_id}/generate-yaml")
        if _gen_resp.status_code == 200:
            _gen_data = _gen_resp.json()
            st.session_state[f"ai_confidence_{pending_id}"] = _gen_data.get("confidence")
            set_yaml_text(_gen_data["yaml"])
        elif _gen_resp.status_code == 404:
            st.error("Fichier source introuvable — impossible de générer le YAML.")
        else:
            st.error(f"Erreur IA ({_gen_resp.status_code}) : {_gen_resp.text[:200]}")

    _ai_confidence = st.session_state.get(f"ai_confidence_{pending_id}")
    if _ai_confidence is not None:
        _msg = f"💡 Suggestion de l'IA — confiance : **{_ai_confidence}%**"
        if _ai_confidence >= 70:
            st.success(_msg)
        elif _ai_confidence >= 40:
            st.warning(_msg)
        else:
            st.error(_msg)

    def _sync_yaml_content():
        st.session_state[yaml_content_key] = st.session_state.get(yaml_key, "")

    st.text_area("Mapping YAML", key=yaml_key, height=500, on_change=_sync_yaml_content)
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

with tab_simple:
    _sd = load_yaml(st.session_state[yaml_key])
    _mode_simple = _sd.get("extraction_mode", "table")

    if _mode_simple == "table":
        _guess_header_row = (
            st.session_state.get(f"data_starts_simple_{pending_id}")
            or _sd.get("data_starts_row")
            or 2
        )
        _detected_cols_simple = parse_detected_columns(preview_text, int(_guess_header_row) - 1)
        _new_mapping_simple = render_table_form_simple(
            _sd, pending_id, _detected_cols_simple, sheets_list,
            supplier_guess=meta.get("supplier_guess", ""),
        )
    elif _mode_simple == "matrix":
        _new_mapping_simple = render_matrix_form_simple(
            _sd, pending_id, preview_text, sheets_list,
            supplier_guess=meta.get("supplier_guess", ""),
        )
    elif _mode_simple == "multi_table":
        _new_mapping_simple = render_multi_table_form_simple(
            _sd, pending_id, preview_text, sheets_list,
            supplier_guess=meta.get("supplier_guess", ""),
        )
    else:
        st.info(
            f"Mode d'extraction '{_mode_simple}' non couvert par le formulaire simplifié "
            "— utilisez l'onglet « Formulaire avancé » ou « YAML »."
        )
        _new_mapping_simple = None

    if _new_mapping_simple is not None:
        _new_yaml_simple = dump_yaml(_new_mapping_simple)
        if _new_yaml_simple != st.session_state.get(yaml_content_key):
            _resp_simple = api_put(
                f"/api/v1/review/{pending_id}", {"yaml_content": _new_yaml_simple}
            )
            if _resp_simple.status_code == 200:
                st.session_state[f"simple_status_{pending_id}"] = ("ok", None)
            elif _resp_simple.status_code == 422:
                st.session_state[f"simple_status_{pending_id}"] = (
                    "incomplete", _resp_simple.json().get("detail", [])
                )
            else:
                st.session_state[f"simple_status_{pending_id}"] = ("error", _resp_simple.text)
            set_yaml_text(_new_yaml_simple)

        _status = st.session_state.get(f"simple_status_{pending_id}")
        if _status:
            _kind, _detail = _status
            if _kind == "ok":
                st.success("✅ Configuration enregistrée.")
            elif _kind == "incomplete":
                st.warning("⚠️ Configuration incomplète — complétez les champs manquants :")
                for _err in (_detail or [])[:5]:
                    st.caption(f"- {_err}")
            else:
                st.error(f"Erreur d'enregistrement : {_detail}")

with tab_form:
    current_data = load_yaml(st.session_state[yaml_key])
    extraction_mode = current_data.get("extraction_mode")
    detected_columns = parse_detected_columns(
        preview_text, (current_data.get("header_detection") or {}).get("row")
    )
    renderer = FORM_RENDERERS.get(extraction_mode)
    if renderer is None:
        st.info(
            f"Mode d'extraction '{extraction_mode}' non reconnu — éditez via l'onglet YAML."
        )
    else:
        new_mapping = renderer(current_data, pending_id, detected_columns)
        if new_mapping is not None:
            new_yaml_text = dump_yaml(new_mapping)
            resp = api_put(f"/api/v1/review/{pending_id}", {"yaml_content": new_yaml_text})
            if resp.status_code == 200:
                st.success("Formulaire enregistré et validé.")
                set_yaml_text(new_yaml_text)
            elif resp.status_code == 422:
                st.error("Configuration invalide :")
                for err in resp.json().get("detail", []):
                    st.write(f"- {err}")
            else:
                st.error(f"Erreur {resp.status_code} : {resp.text}")

with tab_ai:
    st.caption(
        "Demande à l'IA de modifier le mapping en langage naturel. Ses changements "
        "s'appliquent au YAML — donc au formulaire et à l'aperçu."
    )
    with st.expander("📩 Prompt initial envoyé à l'IA"):
        st.code(meta.get("initial_prompt") or "(non disponible)", language=None)

    chat_key = f"chat_{pending_id}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []
    for role, content in st.session_state[chat_key]:
        with st.chat_message(role):
            st.markdown(content)

    instruction = st.text_input(
        "Ta demande (ex. « mets les dates en E4/J4 format FR », « le prix est en colonne G »)",
        key=f"ai_instr_{pending_id}",
    )
    if st.button("Envoyer à l'IA", key=f"ai_send_{pending_id}") and instruction.strip():
        st.session_state[chat_key].append(("user", instruction.strip()))
        with st.spinner("L'IA met à jour le YAML…"):
            resp = api_post(
                f"/api/v1/review/{pending_id}/ai-edit",
                {"yaml_content": st.session_state[yaml_key], "instruction": instruction.strip()},
            )
        if resp.status_code == 200:
            data = resp.json()
            if data["valid"]:
                st.session_state[chat_key].append((
                    "assistant",
                    "✅ YAML mis à jour. Vérifie les onglets **YAML**, **Formulaire** "
                    "et **Aperçu export Gery**.",
                ))
            else:
                errs = "\n".join(f"- {e}" for e in data["errors"])
                st.session_state[chat_key].append((
                    "assistant",
                    f"⚠️ YAML modifié mais **invalide** :\n{errs}\n\nRedemande-moi une correction.",
                ))
            set_yaml_text(data["yaml"])
        else:
            st.session_state[chat_key].append(
                ("assistant", f"Erreur {resp.status_code} : {resp.text[:200]}")
            )
            st.rerun()

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

# Téléchargement des fichiers Gery générés (après validation)
exports = meta.get("exports") or []
if meta["status"] == "approved" and exports:
    st.divider()
    st.markdown("#### 📥 Fichiers Gery générés (CSV)")
    for fname in exports:
        try:
            data = fetch_export_bytes(pending_id, fname)
            st.download_button(
                label=f"Télécharger {fname}",
                data=data,
                file_name=fname,
                mime="text/csv",
                key=f"dl_{pending_id}_{fname}",
            )
        except Exception as exc:
            st.warning(f"Téléchargement indisponible pour {fname} : {exc}")
