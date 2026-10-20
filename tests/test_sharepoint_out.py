"""Dépôt des exports Gery dans le dossier Out de SharePoint (Graph simulé, aucun appel réseau)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import httpx
import pytest

from middleware import pipeline
from middleware.core.config import Settings
from middleware.exporter.gery import GeneratedFile, GeryExportResult
from middleware.storage import sharepoint_out as spo
from middleware.storage.sharepoint_out import (
    CHUNK_BYTES,
    SIMPLE_UPLOAD_MAX_BYTES,
    DeliveryResult,
    SharePointOutClient,
    SharePointOutError,
)


def _settings(**over) -> Settings:
    base = dict(
        sharepoint_out_enabled=True,
        sharepoint_out_host="contoso.sharepoint.com",
        sharepoint_out_site_path="/sites/exports-gery",
        sharepoint_out_folder="Out",
        azure_tenant_id="t", azure_client_id="c", azure_client_secret="s",
    )
    base.update(over)
    return Settings.model_construct(**base)


class FakeGraph:
    """Faux Graph : enregistre les requêtes et répond comme SharePoint."""

    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.requests: list[httpx.Request] = []
        self.chunks: list[tuple[str, int]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url, path = str(request.url), unquote(request.url.path)
        if request.method == "GET" and "/sites/contoso.sharepoint.com:" in path:
            return httpx.Response(200, json={"id": "site-1"})
        if request.method == "GET" and path.endswith("/sites/site-1/drive"):
            return httpx.Response(200, json={"id": "drive-1"})
        if request.method == "PUT" and path.endswith(":/content"):
            name = path.split("/root:/")[1].removesuffix(":/content")
            if name in self.existing:
                return httpx.Response(409, json={"error": {"code": "nameAlreadyExists", "message": "x"}})
            return httpx.Response(201, json={"webUrl": f"https://sp/{name}"})
        if request.method == "POST" and path.endswith(":/createUploadSession"):
            name = path.split("/root:/")[1].removesuffix(":/createUploadSession")
            if name in self.existing:
                return httpx.Response(409, json={"error": {"code": "nameAlreadyExists", "message": "x"}})
            return httpx.Response(200, json={"uploadUrl": "https://upload.example/session/1"})
        if request.method == "PUT" and url.startswith("https://upload.example/"):
            self.chunks.append((request.headers["Content-Range"], len(request.content)))
            total = int(request.headers["Content-Range"].split("/")[1])
            last = int(request.headers["Content-Range"].split("-")[1].split("/")[0]) == total - 1
            if last:
                return httpx.Response(201, json={"webUrl": "https://sp/gros.csv"})
            return httpx.Response(202, json={})
        return httpx.Response(500, json={"error": {"code": "inattendu", "message": url}})


def _client(graph: FakeGraph, **over) -> SharePointOutClient:
    http = httpx.Client(transport=httpx.MockTransport(graph))
    return SharePointOutClient(_settings(**over), http=http, token_provider=lambda: "jeton")


def test_depot_simple_dans_out_sans_ecraser(tmp_path: Path) -> None:
    f = tmp_path / "NEW_ARTICLE_atlantic_20261020-120000.csv"
    f.write_text("a||b\n1||2\n", encoding="utf-8")
    graph = FakeGraph()

    url = _client(graph).upload(f)

    assert url == "https://sp/Out/NEW_ARTICLE_atlantic_20261020-120000.csv"
    put = next(r for r in graph.requests if r.method == "PUT")
    assert unquote(put.url.path).endswith("/drives/drive-1/root:/Out/NEW_ARTICLE_atlantic_20261020-120000.csv:/content")
    assert put.url.params["@microsoft.graph.conflictBehavior"] == "fail"
    assert put.headers["Authorization"] == "Bearer jeton"
    assert put.content == f.read_bytes()


def test_fichier_deja_present_n_est_pas_ecrase(tmp_path: Path) -> None:
    f = tmp_path / "deja.csv"
    f.write_text("x", encoding="utf-8")
    client = _client(FakeGraph(existing={"Out/deja.csv"}))
    with pytest.raises(spo.FileAlreadyExistsError):
        client.upload(f)


def test_gros_fichier_envoye_par_blocs_sans_en_tete_auth(tmp_path: Path) -> None:
    f = tmp_path / "gros.csv"
    size = SIMPLE_UPLOAD_MAX_BYTES + CHUNK_BYTES + 123
    f.write_bytes(b"x" * size)
    graph = FakeGraph()

    assert _client(graph).upload(f) == "https://sp/gros.csv"

    assert sum(n for _, n in graph.chunks) == size
    assert len(graph.chunks) == 3
    assert all(n % (320 * 1024) == 0 for _, n in graph.chunks[:-1])  # blocs multiples de 320 Kio
    ranges = [r.split()[1].split("/")[0] for r, _ in graph.chunks]
    assert ranges[0].startswith("0-")
    uploads = [r for r in graph.requests if str(r.url).startswith("https://upload.example/")]
    assert all("Authorization" not in r.headers for r in uploads)


def test_site_non_configure(tmp_path: Path) -> None:
    f = tmp_path / "a.csv"
    f.write_text("x", encoding="utf-8")
    graph = FakeGraph()
    with pytest.raises(SharePointOutError, match="non renseign"):
        _client(graph, sharepoint_out_site_path="").upload(f)
    assert graph.requests == []  # aucun appel réseau tant que la config est incomplète


def test_identifiants_absents() -> None:
    client = SharePointOutClient(_settings(azure_client_secret=""), http=httpx.Client())
    with pytest.raises(SharePointOutError, match="identifiants Azure absents"):
        client._credential()


# ── Intégration pipeline : statut en base ────────────────────────────────────

def _export_result(tmp_path: Path) -> tuple[GeryExportResult, dict[Path, SimpleNamespace]]:
    p = tmp_path / "NEW_ARTICLE_x.csv"
    p.write_text("a\n1\n", encoding="utf-8")
    result = GeryExportResult(files=[GeneratedFile("NEW_ARTICLE", p, 1, "h")])
    db = {p: SimpleNamespace(status="generated", delivered_at=None, ack_message=None)}
    return result, db


def _patch(monkeypatch, enabled: bool, outcome: DeliveryResult, calls: list) -> None:
    monkeypatch.setattr(pipeline, "get_settings", lambda: SimpleNamespace(sharepoint_out_enabled=enabled))

    async def fake_deliver(path: Path) -> DeliveryResult:
        calls.append(path)
        return outcome

    monkeypatch.setattr(pipeline, "deliver_file", fake_deliver)


def test_pipeline_desactive_ne_fait_rien(tmp_path: Path, monkeypatch) -> None:
    result, db = _export_result(tmp_path)
    calls: list = []
    _patch(monkeypatch, False, DeliveryResult(ok=True), calls)
    asyncio.run(pipeline.deliver_exports_to_sharepoint(result, db))
    assert calls == []
    assert next(iter(db.values())).status == "generated"


def test_pipeline_succes_marque_delivered(tmp_path: Path, monkeypatch) -> None:
    result, db = _export_result(tmp_path)
    _patch(monkeypatch, True, DeliveryResult(ok=True, web_url="https://sp/x"), [])
    asyncio.run(pipeline.deliver_exports_to_sharepoint(result, db))
    row = next(iter(db.values()))
    assert row.status == "delivered" and row.delivered_at is not None


def test_pipeline_echec_marque_failed_sans_lever(tmp_path: Path, monkeypatch) -> None:
    result, db = _export_result(tmp_path)
    _patch(monkeypatch, True, DeliveryResult(ok=False, error="403 refusé"), [])
    asyncio.run(pipeline.deliver_exports_to_sharepoint(result, db))
    row = next(iter(db.values()))
    assert row.status == "failed" and row.ack_message == "403 refusé" and row.delivered_at is None
