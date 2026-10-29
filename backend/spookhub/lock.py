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
ALLOWED_PREFIX = ("/api/show/", "/api/auth/", "/api/preflight/")
ALLOWED_BOARD = re.compile(r"^/api/boards/[^/]+/(state|identify)$")
LIVE_SETTINGS = {"master_brightness", "master_volume"}


class LockMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, is_locked: Callable[[], bool]) -> None:
        super().__init__(app)
        self.is_locked = is_locked

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path.rstrip("/") or "/"
        if (
            request.method not in SAFE_METHODS
            and path.startswith("/api/")
            and self.is_locked()
            and not await self._allowed(request, path)
        ):
            return JSONResponse({"detail": "Show mode is on"}, status_code=423)
        return await call_next(request)

    async def _allowed(self, request: Request, path: str) -> bool:
        if path in ALLOWED_EXACT or path.startswith(ALLOWED_PREFIX) or ALLOWED_BOARD.match(path):
            return True
        if path == "/api/settings" and request.method == "PUT":
            try:
                body = json.loads(await request.body())
            except ValueError:
                return False
            return isinstance(body, dict) and set(body) <= LIVE_SETTINGS
        return False
