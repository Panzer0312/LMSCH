"""Show lock: while on, mutating /api requests are refused except the show-time allowlist."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ALLOWED_EXACT = {"/api/trigger", "/api/blackout", "/api/show_lock"}
ALLOWED_PREFIX = ("/api/preflight/",)
ALLOWED_SHOW = {
    f"/api/show/{n}" for n in ("trigger", "play", "stop", "seek", "pause", "next", "deactivate")
} | {"/api/auth/login", "/api/auth/logout"}
MAX_BODY = 4096
ALLOWED_BOARD = re.compile(r"^/api/boards/[^/]+/(state|identify)$")
LIVE_SETTINGS = {"master_brightness", "master_volume"}


class LockMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, is_locked: Callable[[], bool]) -> None:
        super().__init__(app)
        self.is_locked = is_locked

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path.rstrip("/") or "/"
        if request.method not in SAFE_METHODS and path.startswith("/api/") and self.is_locked():
            refusal = await self._refusal(request, path)
            if refusal is not None:
                return refusal
        return await call_next(request)

    async def _refusal(self, request: Request, path: str) -> Response | None:
        """None when the request may pass the lock."""
        locked = JSONResponse({"detail": "Show mode is on"}, status_code=423)
        if path in ALLOWED_EXACT or path in ALLOWED_SHOW or path.startswith(ALLOWED_PREFIX):
            return None
        if ALLOWED_BOARD.match(path):
            return None
        if path == "/api/settings" and request.method == "PUT":
            too_big = JSONResponse({"detail": "Body too large"}, status_code=413)
            try:
                declared = int(request.headers.get("content-length") or 0)
            except ValueError:
                return locked
            if declared > MAX_BODY:
                return too_big
            raw = await request.body()
            if len(raw) > MAX_BODY:
                return too_big
            try:
                body = json.loads(raw)
            except ValueError:
                return locked
            if isinstance(body, dict) and set(body) <= LIVE_SETTINGS:
                return None
        return locked
