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
ALLOWED_EXACT = {
    "/api/trigger",
    "/api/blackout",
    "/api/show_lock",
    "/api/scene/skip",
    "/api/scene/pin",
    "/api/sounds/play",
    "/api/sounds/stop",
    "/api/moments/stop",
}
ALLOWED_SHOW = {
    f"/api/show/{n}" for n in ("trigger", "play", "stop", "seek", "pause", "next", "deactivate")
} | {"/api/auth/login", "/api/auth/logout"}
# The party keeps going in Show mode; /api/jukebox_reset is not under this prefix.
ALLOWED_PREFIX = "/api/jukebox/"
MAX_BODY = 4096
ALLOWED_BOARD = re.compile(r"^/api/boards/[^/]+/(state|identify)$")
# Room-wide Auto / Off / White on /show, same as the per-board quick controls.
ALLOWED_GROUP = re.compile(r"^/api/groups/\d+/(state|identify)$")
# Pressing a moment is show-time; editing / reordering moments is not.
ALLOWED_MOMENT = re.compile(r"^/api/moments/\d+/play$")
LIVE_SETTINGS = {"master_brightness", "master_volume"}
# Turning these OFF stays possible so a forgotten setup action can be cleared from Show mode.
OFF_ONLY = {"/api/setup/calibration", "/api/show/test_pattern", "/api/display/setup"}


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
        if (
            path in ALLOWED_EXACT
            or path in ALLOWED_SHOW
            or ALLOWED_BOARD.match(path)
            or ALLOWED_GROUP.match(path)
            or ALLOWED_MOMENT.match(path)
            or path.startswith(ALLOWED_PREFIX)
        ):
            return None
        if path == "/api/settings" and request.method == "PUT":
            body = await self._json(request)
            if isinstance(body, Response):
                return body
            if isinstance(body, dict) and set(body) <= LIVE_SETTINGS:
                return None
        elif path in OFF_ONLY and request.method == "POST":
            body = await self._json(request)
            if isinstance(body, Response):
                return body
            if isinstance(body, dict) and body.get("on") is False:
                return None
        return locked

    async def _json(self, request: Request) -> Any:
        """Parsed body, None when unparsable, or a 413 response. Bounded read."""
        too_big = JSONResponse({"detail": "Body too large"}, status_code=413)
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            return None
        if declared > MAX_BODY:
            return too_big
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():  # bounded: never buffer more than MAX_BODY
            size += len(chunk)
            if size > MAX_BODY:
                return too_big
            chunks.append(chunk)
        raw = b"".join(chunks)
        request._body = raw  # let the route (via the cached request) read it again
        try:
            return json.loads(raw)
        except ValueError:
            return None
