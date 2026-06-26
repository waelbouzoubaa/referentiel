"""Interface de validation des mappings YAML générés par IA pour les fournisseurs inconnus.

Permet de relire l'aperçu du fichier Excel source, d'éditer le YAML proposé
(directement ou via un formulaire simplifié pour le mode 'table'), puis de
valider (génère les exports Gery) ou de rejeter la proposition.
"""
from __future__ import annotations

import base64
import io
import os
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


def fetch_preview(pending_id: str) -> str:
    resp = api_get(f"/api/v1/review/{pending_id}/preview")
    if resp.status_code != 200:
        return f"(Aperçu indisponible : {resp.text})"
    return resp.json().get("preview", "")


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
    tab_guide, tab_runbook = st.tabs(
        ["📝 Créer un YAML (table simple)", "🛠️ Runbook (opérer / dépanner)"]
    )
    with tab_guide:
        doc = _DOCS_DIR / "GUIDE_YAML_TABLE.md"
        if doc.exists():
            st.markdown(doc.read_text(encoding="utf-8"))
        else:
            st.info("Guide non monté dans le conteneur.")
            st.link_button("Ouvrir le guide sur GitHub", f"{_GITHUB_DOCS}/GUIDE_YAML_TABLE.md")
    with tab_runbook:
        doc = _DOCS_DIR / "RUNBOOK.md"
        if doc.exists():
            st.markdown(doc.read_text(encoding="utf-8"))
        else:
            st.info("Runbook non monté dans le conteneur.")
            st.link_button("Ouvrir le runbook sur GitHub", f"{_GITHUB_DOCS}/RUNBOOK.md")


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
    format_func=lambda i: "{}\n{}".format(
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

preview_text = fetch_preview(pending_id)

yaml_key = f"yaml_text_{pending_id}"
if yaml_key not in st.session_state:
    st.session_state[yaml_key] = meta["yaml_proposed"]

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
    _cur_yaml = st.session_state[yaml_key]
    _force = st.session_state.pop(f"force_recalc_{pending_id}", False)

    if st.button("🔄 Recalculer l'aperçu", key=f"recalc_preview_{pending_id}"):
        st.session_state[f"force_recalc_{pending_id}"] = True
        st.rerun()

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
    tab_yaml, tab_form, tab_ai = st.tabs(
        ["YAML", "Formulaire simplifié", "🤖 Assistant IA"]
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
                        st.session_state[yaml_key] = _yr.json()["yaml_content"]
                        st.rerun()
                    else:
                        st.error(f"Impossible de charger le YAML ({_yr.status_code}).")
                except Exception as _exc:
                    st.error(f"Erreur : {_exc}")

    # ── Générer le YAML avec l'IA (à la demande) ─────────────────────────
    if st.button(
        "🤖 Générer avec l'IA",
        key=f"gen_ai_{pending_id}",
        help="Analyse le fichier et génère un mapping YAML automatiquement.",
    ):
        with st.spinner("L'IA analyse le fichier…"):
            _gen_resp = api_get(f"/api/v1/review/{pending_id}/generate-yaml")
        if _gen_resp.status_code == 200:
            st.session_state[yaml_key] = _gen_resp.json()["yaml"]
            st.rerun()
        elif _gen_resp.status_code == 404:
            st.error("Fichier source introuvable — impossible de générer le YAML.")
        else:
            st.error(f"Erreur IA ({_gen_resp.status_code}) : {_gen_resp.text[:200]}")

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
                st.session_state[yaml_key] = new_yaml_text
                st.success("Formulaire enregistré et validé.")
                st.rerun()
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
            st.session_state[yaml_key] = data["yaml"]
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
            st.rerun()
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
