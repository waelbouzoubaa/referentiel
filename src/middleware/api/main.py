from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from middleware.core.config import get_settings
from middleware.core.logging import configure_logging

configure_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gestion du cycle de vie de l'application (startup/shutdown)."""
    settings = get_settings()
    logger.info(
        "démarrage du middleware",
        environment=settings.environment,
        version="0.1.0",
    )
    yield
    logger.info("arrêt du middleware")


def create_app() -> FastAPI:
    """Fabrique l'instance FastAPI."""
    settings = get_settings()

    app = FastAPI(
        title="Middleware Ramery",
        description="Normalisation adaptative des catalogues fournisseurs",
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from middleware.api.routes import health, suppliers, processing, products
    app.include_router(health.router)
    app.include_router(suppliers.router, prefix="/suppliers", tags=["fournisseurs"])
    app.include_router(processing.router, prefix="/api/v1", tags=["traitement"])
    app.include_router(products.router, prefix="/products", tags=["produits"])

    return app


app = create_app()
