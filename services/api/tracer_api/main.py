"""FastAPI application factory. Run with ``uvicorn --factory tracer_api.main:app_factory``."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from tracer_core import db
from tracer_core import log as tlog
from tracer_core.config import Settings, get_settings
from tracer_core.storage import Storage, from_settings

from . import errors
from .auth import TokenVerifier
from .routers import clips, me
from .schemas import Health

logger = logging.getLogger("tracer_api")

try:
    VERSION = version("tracer-api")
except PackageNotFoundError:  # pragma: no cover
    VERSION = "0.0.0"


def create_app(
    settings: Settings | None = None,
    *,
    storage: Storage | None = None,
    verifier: TokenVerifier | None = None,
    manage_pool: bool = True,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if manage_pool:
            db.open_pool(settings.database_url)
        try:
            yield
        finally:
            if manage_pool:
                db.close_pool()

    app = FastAPI(
        title="Golf Tracer API",
        version=VERSION,
        lifespan=lifespan,
        # One schema per model, so generated clients see the same types for requests and responses.
        separate_input_output_schemas=False,
        description="REST API for the Golf Tracer web and mobile clients. Authenticate with a Supabase access token.",
    )
    app.state.settings = settings
    app.state.storage = storage or from_settings()
    app.state.verifier = verifier or TokenVerifier(
        jwks_url=settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json",
        audience=settings.supabase_jwt_audience,
        hs256_secret=settings.supabase_jwt_secret,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Retry-After"],
        max_age=600,
    )
    errors.install(app)

    @app.middleware("http")
    async def access_log(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        response: Response = await call_next(request)
        if request.url.path != "/health":
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
        return response

    @app.get("/health", response_model=Health, tags=["meta"], operation_id="health")
    def health() -> Health:
        state = "ok"
        try:
            with db.pool().connection() as conn:
                conn.execute("select 1")
        except Exception:
            state = "unavailable"
        return Health(status="ok", version=VERSION, database=state)  # type: ignore[arg-type]

    app.include_router(clips.router)
    app.include_router(me.router)
    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory tracer_api.main:app_factory``."""
    tlog.setup()
    return create_app()
