"""FastAPI app factory and entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from spookhub.api import routes_boards, routes_media, routes_profiles, routes_show, ws
from spookhub.config import Config, get_config
from spookhub.hub import Hub


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
    for r in (routes_boards, routes_profiles, routes_show, routes_media, ws):
        app.include_router(r.router)

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
