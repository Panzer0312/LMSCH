"""Spotify Web API client: PKCE login, token refresh, search and player control.

Tokens live in the jukebox Setting row; the service hands them in and gets them back through
`on_tokens`. Nothing here logs tokens.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

SCOPES = (
    "user-read-playback-state user-modify-playback-state user-read-currently-playing "
    "user-read-private"
)
REFRESH_EARLY_S = 60.0
RETRY_AFTER_CAP_S = 10.0
TIMEOUT_S = 8.0


class SpotifyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def track_info(t: dict[str, Any]) -> dict[str, Any]:
    """Spotify track object -> JukeTrack."""
    album = t.get("album") or {}
    images = [i for i in album.get("images") or [] if i.get("url")]
    # Phones show ~64-300 px art: pick the smallest image that is still >= 300 px wide.
    images.sort(key=lambda i: i.get("width") or 0)
    art = next((i["url"] for i in images if (i.get("width") or 0) >= 300), None)
    if art is None and images:
        art = images[-1]["url"]
    return {
        "uri": str(t.get("uri") or ""),
        "title": str(t.get("name") or "?"),
        "artist": ", ".join(str(a.get("name")) for a in t.get("artists") or [] if a.get("name")),
        "album": str(album.get("name") or ""),
        "art_url": art,
        "duration_ms": int(t.get("duration_ms") or 0),
        "explicit": bool(t.get("explicit")),
    }


class SpotifyClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        on_tokens: Callable[[dict[str, Any] | None], None],
        accounts_url: str = "https://accounts.spotify.com",
        api_url: str = "https://api.spotify.com/v1",
    ) -> None:
        self.http = http
        self.on_tokens = on_tokens
        self.accounts_url = accounts_url.rstrip("/")
        self.api_url = api_url.rstrip("/")
        self.client_id: str | None = None
        # {access_token, refresh_token, expires_at (epoch s)}
        self.tokens: dict[str, Any] | None = None
        self._refresh_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return bool(self.client_id and self.tokens and self.tokens.get("refresh_token"))

    # ----- login -----

    def auth_url(self, client_id: str, redirect_uri: str) -> tuple[str, str, str]:
        """(url, state, verifier)."""
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(16)
        q = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
            "scope": SCOPES,
        }
        return f"{self.accounts_url}/authorize?{urlencode(q)}", state, verifier

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> None:
        if not self.client_id:
            raise SpotifyError(400, "Spotify client ID not set")
        data = await self._token_call(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "code_verifier": verifier,
            }
        )
        self._store(data, None)

    async def _refresh(self, stale: str | None) -> None:
        async with self._refresh_lock:
            tok = self.tokens
            if not tok or not self.client_id:
                raise SpotifyError(401, "Spotify not connected")
            # Another caller refreshed while we waited.
            if tok.get("access_token") != stale and tok.get("expires_at", 0) > time.time() + 5:
                return
            try:
                data = await self._token_call(
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": tok["refresh_token"],
                        "client_id": self.client_id,
                    }
                )
            except SpotifyError as exc:
                if exc.status in (400, 401):  # revoked or app changed: log in again
                    self.tokens = None
                    self.on_tokens(None)
                    raise SpotifyError(401, "Spotify login expired, connect again") from exc
                raise
            self._store(data, tok.get("refresh_token"))

    def _store(self, data: dict[str, Any], old_refresh: str | None) -> None:
        if not data.get("access_token"):
            raise SpotifyError(502, "Spotify sent no access token")
        self.tokens = {
            "access_token": data["access_token"],
            # Spotify may or may not rotate the refresh token.
            "refresh_token": data.get("refresh_token") or old_refresh,
            "expires_at": time.time() + float(data.get("expires_in") or 3600),
        }
        self.on_tokens(dict(self.tokens))

    async def _token_call(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            r = await self.http.post(f"{self.accounts_url}/api/token", data=form, timeout=TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise SpotifyError(0, f"Spotify unreachable ({type(exc).__name__})") from exc
        body = _json(r)
        if r.status_code != 200:
            msg = body.get("error_description") or body.get("error") or r.reason_phrase
            raise SpotifyError(r.status_code, str(msg))
        return body

    # ----- API -----

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        if not self.connected:
            raise SpotifyError(401, "Spotify not connected")
        assert self.tokens is not None
        if self.tokens.get("expires_at", 0) < time.time() + REFRESH_EARLY_S:
            await self._refresh(self.tokens.get("access_token"))
        refreshed = throttled = False
        while True:
            tok = (self.tokens or {}).get("access_token")
            try:
                r = await self.http.request(
                    method,
                    f"{self.api_url}{path}",
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    json=body,
                    headers={"Authorization": f"Bearer {tok}"},
                    timeout=TIMEOUT_S,
                )
            except httpx.HTTPError as exc:
                raise SpotifyError(0, f"Spotify unreachable ({type(exc).__name__})") from exc
            if r.status_code == 401 and not refreshed:
                refreshed = True
                await self._refresh(tok)
                continue
            if r.status_code == 429 and not throttled:
                throttled = True
                try:
                    wait = float(r.headers.get("Retry-After") or 1)
                except ValueError:
                    wait = 1.0
                await asyncio.sleep(min(max(wait, 0.0), RETRY_AFTER_CAP_S))
                continue
            if r.status_code in (202, 204) or not r.content:
                if r.status_code >= 400:
                    raise SpotifyError(r.status_code, r.reason_phrase or "error")
                return None
            data = _json(r)
            if r.status_code >= 400:
                err = data.get("error")
                msg = err.get("message") if isinstance(err, dict) else err
                raise SpotifyError(r.status_code, str(msg or r.reason_phrase or "error"))
            return data

    async def me(self) -> dict[str, Any]:
        return await self._request("GET", "/me") or {}

    async def search(self, q: str, limit: int = 10) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/search",
            {"type": "track", "q": q, "limit": max(1, min(limit, 10)), "market": "from_token"},
        )
        items = ((data or {}).get("tracks") or {}).get("items") or []
        return [track_info(t) for t in items if t and t.get("uri")]

    async def track(self, track_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/tracks/{track_id}", {"market": "from_token"})
        if not data or not data.get("uri"):
            raise SpotifyError(404, "track not found")
        return track_info(data)

    async def devices(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/me/player/devices")
        return [d for d in (data or {}).get("devices") or [] if isinstance(d, dict)]

    async def playback(self) -> dict[str, Any] | None:
        return await self._request("GET", "/me/player")

    async def transfer(self, device_id: str) -> None:
        await self._request("PUT", "/me/player", body={"device_ids": [device_id], "play": False})

    async def play(
        self, device_id: str, uris: list[str] | None = None, context_uri: str | None = None
    ) -> None:
        body: dict[str, Any] = {}
        if uris:
            body["uris"] = uris
        if context_uri:
            body["context_uri"] = context_uri
        await self._request("PUT", "/me/player/play", {"device_id": device_id}, body or None)

    async def resume(self, device_id: str) -> None:
        await self._request("PUT", "/me/player/play", {"device_id": device_id})

    async def pause(self, device_id: str) -> None:
        await self._request("PUT", "/me/player/pause", {"device_id": device_id})

    async def next(self, device_id: str) -> None:
        await self._request("POST", "/me/player/next", {"device_id": device_id})

    async def volume(self, device_id: str, pct: int) -> None:
        pct = max(0, min(100, int(pct)))
        await self._request(
            "PUT", "/me/player/volume", {"volume_percent": pct, "device_id": device_id}
        )

    async def shuffle(self, device_id: str, on: bool) -> None:
        state = "true" if on else "false"
        await self._request("PUT", "/me/player/shuffle", {"state": state, "device_id": device_id})


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
