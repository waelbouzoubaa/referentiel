from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["santé"])


@router.get("/health", summary="Santé du service")
async def health() -> dict[str, str]:
    """Retourne l'état de santé du service."""
    return {"status": "ok", "version": "0.1.0"}
