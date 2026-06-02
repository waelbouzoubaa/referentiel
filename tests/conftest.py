from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from middleware.api.main import app
from middleware.core.config import get_settings


@pytest.fixture
def settings():
    """Retourne les settings de test."""
    return get_settings()


@pytest.fixture
def client() -> TestClient:
    """Client de test synchrone FastAPI."""
    return TestClient(app)


@pytest.fixture
async def async_client() -> AsyncClient:
    """Client de test asynchrone FastAPI."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
