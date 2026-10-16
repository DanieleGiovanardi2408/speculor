"""FastAPI app: `/api/*` routes, CORS, and (if built) the frontend served from `/`.

No route ever submits an application or sends a message.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import Engine

from radar.api import schemas
from radar.api.deps import get_db_engine, require_auth
from radar.api.routes import (
    applications,
    companies,
    connectors,
    contacts,
    drafts,
    packs,
    postings,
    runs,
    settings,
    today,
    usage,
)
from radar.config import REPO_ROOT, get_settings
from radar.db import init_db

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    provider = app.dependency_overrides.get(get_db_engine, get_db_engine)
    engine: Engine = provider()
    init_db(engine)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Radar API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=schemas.Health, tags=["health"])
    def health() -> schemas.Health:
        return schemas.Health(status="ok")

    api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
    modules = (
        today,
        postings,
        packs,
        contacts,
        drafts,
        applications,
        companies,
        settings,
        runs,
        usage,
        connectors,
    )
    for module in modules:
        api.include_router(module.router)
    app.include_router(api)

    if FRONTEND_DIST.is_dir():
        _mount_frontend(app, FRONTEND_DIST)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA after the API routes, with index.html fallback."""
    root = dist.resolve()
    index = root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        if not index.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Frontend not built")
        return FileResponse(index)


app = create_app()
