from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest
from httpx import AsyncClient

from middleware.api.routes import review as review_module

VALID_YAML = """\
supplier_code: "test_fournisseur"
mapping_version: 1
data_starts_row: 2
extraction_mode: table
columns:
  supplier_product_code:
    source_col: "A"
    required: true
  designation:
    source_col: "B"
gery_export:
  enabled: false
  blocked_reason: "test"
"""

# Invalide : data_starts_row (requis) et columns (requis en mode table) manquants
INVALID_YAML = """\
supplier_code: "test_fournisseur"
mapping_version: 1
extraction_mode: table
gery_export:
  enabled: false
  blocked_reason: "test"
"""


def _make_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str = "pending",
    yaml_proposed: str = VALID_YAML,
    file_path: str = "",
) -> str:
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(review_module, "PENDING_DIR", pending_dir)

    pending_id = "abc123"
    meta = {
        "id": pending_id,
        "created_at": "2026-06-15T10:00:00",
        "filename": "nouveau_fournisseur.xlsx",
        "folder_name": "NouveauFournisseur",
        "file_path": file_path,
        "supplier_guess": "test_fournisseur",
        "yaml_proposed": yaml_proposed,
        "status": status,
    }
    (pending_dir / f"{pending_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return pending_id


@pytest.mark.asyncio
async def test_update_pending_yaml_valide(
    async_client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un YAML valide est sauvegardé et le supplier_code est retourné."""
    pending_id = _make_pending(tmp_path, monkeypatch)

    resp = await async_client.put(f"/api/v1/review/{pending_id}", json={"yaml_content": VALID_YAML})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["supplier_code"] == "test_fournisseur"

    saved = json.loads((tmp_path / "pending" / f"{pending_id}.json").read_text(encoding="utf-8"))
    assert saved["yaml_proposed"] == VALID_YAML


@pytest.mark.asyncio
async def test_update_pending_yaml_invalide(
    async_client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un YAML invalide est rejeté (422) et le fichier meta reste inchangé."""
    pending_id = _make_pending(tmp_path, monkeypatch)

    resp = await async_client.put(
        f"/api/v1/review/{pending_id}", json={"yaml_content": INVALID_YAML}
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]

    saved = json.loads((tmp_path / "pending" / f"{pending_id}.json").read_text(encoding="utf-8"))
    assert saved["yaml_proposed"] == VALID_YAML


@pytest.mark.asyncio
async def test_update_pending_yaml_deja_traite(
    async_client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une demande déjà approuvée/rejetée ne peut plus être éditée (409)."""
    pending_id = _make_pending(tmp_path, monkeypatch, status="approved")

    resp = await async_client.put(f"/api/v1/review/{pending_id}", json={"yaml_content": VALID_YAML})

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_pending_preview(
    async_client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'aperçu retourne le contenu textuel du fichier Excel source."""
    excel_path = tmp_path / "source.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Code", "Désignation", "Prix"])
    ws.append(["ABC123", "Produit Test", 12.5])
    wb.save(excel_path)

    pending_id = _make_pending(tmp_path, monkeypatch, file_path=str(excel_path))

    resp = await async_client.get(f"/api/v1/review/{pending_id}/preview")

    assert resp.status_code == 200
    preview = resp.json()["preview"]
    assert "ABC123" in preview
    assert "Produit Test" in preview
