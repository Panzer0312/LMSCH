"""Optional UI PIN (session cookie) and optional trigger token.

No auth by default. When a PIN is set, every /api route and /ws need a valid session cookie,
except login/status and /api/trigger (which has its own optional token, for PIR sensors etc.).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from spookhub import db
from spookhub.models import Setting

KEY = "auth"
COOKIE = "spookhub_session"
OPEN_PATHS = {"/api/auth/login", "/api/auth/status", "/api/auth/logout", "/api/trigger"}


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 100_000).hex()


class AuthStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def load(self) -> None:
        with db.session() as s:
            row = s.get(Setting, KEY)
            self.data = dict(row.value) if row and isinstance(row.value, dict) else {}
        if "secret" not in self.data:
            self.data["secret"] = secrets.token_hex(32)
            self._save()

    def _save(self) -> None:
        with db.session() as s:
            row = s.get(Setting, KEY) or Setting(key=KEY, value={})
            row.value = dict(self.data)
            s.add(row)
            s.commit()

    @property
    def pin_enabled(self) -> bool:
        return bool(self.data.get("pin_hash"))

    @property
    def trigger_token(self) -> str | None:
        return self.data.get("trigger_token") or None

    def set_pin(self, pin: str | None) -> None:
        if pin:
            salt = secrets.token_hex(16)
            self.data["pin_salt"], self.data["pin_hash"] = salt, _hash(pin, salt)
        else:
            self.data.pop("pin_salt", None)
            self.data.pop("pin_hash", None)
        # Rotating the secret logs out every existing session.
        self.data["secret"] = secrets.token_hex(32)
        self._save()

    def set_trigger_token(self, token: str | None) -> None:
        self.data["trigger_token"] = token or None
        self._save()

    def check_pin(self, pin: str) -> bool:
        if not self.pin_enabled:
            return True
        return hmac.compare_digest(_hash(pin, self.data["pin_salt"]), self.data["pin_hash"])

    def session_token(self) -> str:
        return hmac.new(
            self.data["secret"].encode(), self.data.get("pin_hash", "").encode(), "sha256"
        ).hexdigest()

    def valid_session(self, token: str | None) -> bool:
        if not self.pin_enabled:
            return True
        return bool(token) and hmac.compare_digest(token or "", self.session_token())

    def valid_trigger(self, token: str | None) -> bool:
        expected = self.trigger_token
        return expected is None or (token is not None and hmac.compare_digest(token, expected))


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, store: AuthStore) -> None:
        super().__init__(app)
        self.store = store

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path.rstrip("/") or "/"
        guarded = path.startswith("/api/") and path not in OPEN_PATHS
        if guarded and not self.store.valid_session(request.cookies.get(COOKIE)):
            return JSONResponse({"detail": "PIN required"}, status_code=401)
        return await call_next(request)
