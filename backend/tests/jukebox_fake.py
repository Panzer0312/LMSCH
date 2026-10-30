"""In-memory Spotify Web API for jukebox tests (served through httpx.MockTransport)."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs

import httpx

from spookhub import db
from spookhub.config import Config
from spookhub.events import EventBus
from spookhub.jukebox.service import JukeboxService

DEVICE = {"id": "dev1", "name": "Spookhub", "type": "Speaker", "is_active": False}


def tid(n: int) -> str:
    return f"{n:022d}".replace("0", "A")


def track(n: int, duration_ms: int = 180_000, explicit: bool = False) -> dict[str, Any]:
    return {
        "uri": f"spotify:track:{tid(n)}",
        "name": f"Song {n}",
        "artists": [{"name": f"Artist {n}"}],
        "album": {"name": "Album", "images": [{"url": f"http://img/{n}", "width": 300}]},
        "duration_ms": duration_ms,
        "explicit": explicit,
    }


class FakeSpotify:
    def __init__(self) -> None:
        self.tracks: dict[str, dict[str, Any]] = {tid(n): track(n) for n in range(1, 10)}
        self.devices = [dict(DEVICE)]
        self.playback: dict[str, Any] | None = None
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []
        self.token_forms: list[dict[str, list[str]]] = []
        # path -> list of statuses returned (once each) before normal handling
        self.fail: dict[str, list[int]] = {}
        self.fallback_item = track(99)
        self.product = "premium"

    def play_calls(self) -> list[Any]:
        return [b for m, p, _, b in self.calls if m == "PUT" and p == "/me/player/play"]

    def paths(self) -> list[str]:
        return [f"{m} {p}" for m, p, _, _ in self.calls]

    def set_playing(self, n: int | dict[str, Any], progress_ms: int, playing: bool = True) -> None:
        item = track(n) if isinstance(n, int) else n
        self.playback = {
            "device": dict(DEVICE),
            "item": item,
            "is_playing": playing,
            "progress_ms": progress_ms,
        }

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/token":
            form = parse_qs(req.content.decode())
            self.token_forms.append(form)
            if self.fail.get("/api/token"):
                return httpx.Response(self.fail["/api/token"].pop(0), json={"error": "bad"})
            n = len(self.token_forms)
            return httpx.Response(
                200, json={"access_token": f"AT{n}", "refresh_token": f"RT{n}", "expires_in": 3600}
            )
        path = req.url.path.removeprefix("/v1")
        params = dict(req.url.params)
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, path, params, body))
        if self.fail.get(path):
            return httpx.Response(self.fail[path].pop(0), json={"error": {"message": "nope"}})
        key = f"{req.method} {path}"
        if key == "GET /me":
            return httpx.Response(200, json={"display_name": "Host", "product": self.product})
        if key == "GET /search":
            q = params.get("q", "").lower()
            items = [t for t in self.tracks.values() if q in t["name"].lower()]
            return httpx.Response(200, json={"tracks": {"items": items[: int(params["limit"])]}})
        if req.method == "GET" and path.startswith("/tracks/"):
            t = self.tracks.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=t) if t else httpx.Response(404, json={})
        if key == "GET /me/player/devices":
            return httpx.Response(200, json={"devices": self.devices})
        if key == "GET /me/player":
            if self.playback is None:
                return httpx.Response(204)
            return httpx.Response(200, json=self.playback)
        if key == "PUT /me/player/play":
            if body and body.get("uris"):
                self.set_playing(self.tracks[body["uris"][0].rsplit(":", 1)[1]], 0)
            elif body and body.get("context_uri"):
                self.set_playing(self.fallback_item, 0)
            elif self.playback:
                self.playback["is_playing"] = True
            return httpx.Response(204)
        if key == "PUT /me/player/pause":
            if self.playback:
                self.playback["is_playing"] = False
            return httpx.Response(204)
        if key in (
            "PUT /me/player",
            "POST /me/player/next",
            "PUT /me/player/volume",
            "PUT /me/player/shuffle",
        ):
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"message": f"no route {key}"}})


def make_service(cfg: Config) -> tuple[JukeboxService, FakeSpotify, dict[str, bool]]:
    db.init_engine(cfg.paths.db_path)
    fake = FakeSpotify()
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    busy = {"v": False}
    svc = JukeboxService(cfg, EventBus(), http, show_busy=lambda: busy["v"])
    svc.load()
    svc.START_GRACE_S = 0.0
    svc.SKIP_DEBOUNCE_S = 0.0
    return svc, fake, busy


def connect(svc: JukeboxService) -> None:
    svc.spotify.client_id = "a" * 32
    svc.spotify.tokens = {
        "access_token": "AT0",
        "refresh_token": "RT0",
        "expires_at": time.time() + 3600,
    }
    svc._save()
