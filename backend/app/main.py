"""FastAPI application entry point.

Run from the backend folder:
    uvicorn app.main:app --host 127.0.0.1 --port 8000
API docs: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.api import routes_core, routes_demo, routes_monitoring, routes_registrations
from app.core.config import Settings
from app.core.errors import RaceSafeError
from app.system import RaceSafeSystem

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logger = logging.getLogger("racesafe")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Sync endpoints run in AnyIO's thread pool (default 40 threads). Raise
        # it so a 500-request burst is accepted quickly; the 8 registration
        # workers are a separate, fixed pool.
        anyio.to_thread.current_default_thread_limiter().total_tokens = 200
        system = RaceSafeSystem(settings)
        app.state.system = system
        system.start()
        logger.info("RaceSafe started: mode=%s workers=%d env=%s db=%s",
                    settings.scheduler_mode, settings.worker_count, settings.environment, settings.db_name)
        try:
            yield
        finally:
            system.stop()

    app = FastAPI(
        title="RaceSafe API",
        version="1.0.0",
        description="Fair and fault-tolerant course registration (OS Hackathon - PS OSH001).",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RaceSafeError)
    async def handle_app_error(_: Request, exc: RaceSafeError):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "detail": exc.message})

    @app.exception_handler(DBAPIError)
    async def handle_db_error(_: Request, exc: DBAPIError):
        if isinstance(exc, (OperationalError, InterfaceError)):
            logger.warning("Database unavailable: %s", exc.orig)
            return JSONResponse(status_code=503, content={
                "code": "DATABASE_UNAVAILABLE",
                "detail": f"Database error: {exc.orig}. Is MySQL running and is backend/.env correct?",
            })
        logger.exception("Database error")
        return JSONResponse(status_code=500, content={"code": "DATABASE_ERROR", "detail": str(exc.orig)[:300]})

    @app.exception_handler(PoolTimeoutError)
    async def handle_pool_timeout(_: Request, exc: PoolTimeoutError):
        return JSONResponse(status_code=503, content={"code": "BUSY", "detail": "All database connections are busy"})

    for module in (routes_core, routes_registrations, routes_monitoring, routes_demo):
        app.include_router(module.router, prefix="/api")

    @app.get("/", include_in_schema=False)
    def root():
        return {"name": "RaceSafe API", "docs": "/docs", "health": "/api/health"}

    return app


app = create_app()
