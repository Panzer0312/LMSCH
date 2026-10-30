"""FastAPI app factory and entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from spookhub.api import (
    routes_admin,
    routes_auth,
    routes_boards,
    routes_display,
    routes_events,
    routes_media,
    routes_patterns,
    routes_profiles,
    routes_scene,
    routes_setup,
    routes_show,
    routes_sounds,
    routes_templates,
    ws,
)
from spookhub.auth import AuthMiddleware
from spookhub.config import Config, get_config
from spookhub.hub import Hub
from spookhub.lock import LockMiddleware


def setup_logging(level: str) -> None:
    # journald captures stderr; keep lines single-line key=value-ish for grepping.
    logging.basicConfig(
        level=level.upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Per-request lines from httpx would flood journald at 3 s health intervals.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or get_config()
    hub = Hub(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(title="Spookhub", version="0.1.0", lifespan=lifespan)
    app.state.hub = hub

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # The default handler echoes the rejected input; a NaN there makes the 422 itself
        # fail to serialize (500). Drop the echo.
        errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    # Last added runs first: auth must answer 401 before the lock answers 423.
    app.add_middleware(LockMiddleware, is_locked=lambda: hub.settings.value.show_lock)
    app.add_middleware(AuthMiddleware, store=hub.auth)
    for r in (
        routes_auth,
        routes_admin,
        routes_boards,
        routes_display,
        routes_profiles,
        routes_setup,
        routes_show,
        routes_sounds,
        routes_events,
        routes_media,
        routes_patterns,
        routes_templates,
        routes_scene,
        ws,
    ):
        app.include_router(r.router)
    app.include_router(routes_setup.preflight_router)
    app.include_router(routes_display.media_router)

    dist = cfg.server.frontend_dir
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


def run() -> None:
    cfg = get_config()
    setup_logging(cfg.server.log_level)
    uvicorn.run(create_app(cfg), host=cfg.server.host, port=cfg.server.port, log_level="info")


if __name__ == "__main__":
    run()
