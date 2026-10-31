"""Party jukebox: guest registry, fair queue, Spotify player loop.

The hub keeps its own queue (Spotify's queue API can neither remove nor reorder) and plays one
track at a time on the librespot device, advancing itself shortly before a track ends.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import secrets
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from spookhub import db
from spookhub.config import Config
from spookhub.events import EventBus
from spookhub.jukebox.fairqueue import ordered
from spookhub.jukebox.spotify import SpotifyClient, SpotifyError, track_info
from spookhub.models import Setting
from spookhub.tasks import spawn

log = logging.getLogger(__name__)

KEY = "jukebox"
DEFAULT_SETTINGS: dict[str, Any] = {
    "accepting": True,
    "per_guest_limit": 3,
    "allow_explicit": True,
    "fallback_uri": None,
    "volume": 70,
    "skip_vote_ratio": 0.5,
    "max_track_min": 10,
}
_ID = r"([A-Za-z0-9]{22})"
_TRACK_URI = re.compile(rf"^spotify:track:{_ID}$")
_CONTEXT_URI = re.compile(rf"^spotify:(playlist|album):{_ID}$")
_URL = re.compile(
    rf"^(?:https?://)?open\.spotify\.com/(?:intl-[A-Za-z-]+/)?(track|playlist|album)/{_ID}(?:[/?#].*)?$"
)
_CLIENT_ID = re.compile(r"^[A-Za-z0-9]{16,64}$")


class JukeboxError(Exception):
    """Refusal with an HTTP status and a guest-friendly detail."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def parse_track_id(text: str) -> str | None:
    """spotify:track:ID, open.spotify.com/track/ID?si=…, open.spotify.com/intl-xx/track/ID."""
    s = text.strip()
    m = _TRACK_URI.match(s)
    if m:
        return m.group(1)
    m = _URL.match(s)
    return m.group(2) if m and m.group(1) == "track" else None


def normalize_context(text: str | None) -> str | None:
    """Playlist/album URI or URL -> spotify:playlist:ID / spotify:album:ID. ValueError if not."""
    s = (text or "").strip()
    if not s:
        return None
    m = _CONTEXT_URI.match(s) or _URL.match(s)
    if not m or m.group(1) not in ("playlist", "album"):
        raise ValueError("Use a Spotify playlist or album link")
    return f"spotify:{m.group(1)}:{m.group(2)}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def probe_port80(timeout: float = 0.3) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", 80), timeout)
    except (OSError, TimeoutError):
        return False
    w.close()
    return True


class JukeboxService:
    ACTIVE_S = 1800.0
    NEAR_END_MS = 4000
    END_MARGIN_MS = 300
    # Spotify's player state lags a PUT play by a second or two.
    START_GRACE_S = 6.0
    QUEUE_MAX = 200
    HISTORY_MAX = 20
    GUESTS_MAX = 500
    PLAY_FAILS_MAX = 3
    SKIP_DEBOUNCE_S = 1.5
    BACKOFF_MIN_S = 5.0
    BACKOFF_MAX_S = 60.0
    # A track that stops before half of it played, with nothing else on the speaker, was cut by
    # the speaker (librespot exits when it can't open the audio device). Retry it, don't burn the
    # queue through a dead speaker.
    LOST_RETRY_S = 10.0
    LOST_PROGRESS_MS = 3000

    def __init__(
        self,
        cfg: Config,
        bus: EventBus,
        http: httpx.AsyncClient,
        show_busy: Callable[[], bool],
    ) -> None:
        self.jc = cfg.jukebox
        self.bus = bus
        self.show_busy = show_busy
        self.redirect_uri = (
            self.jc.redirect_uri
            or f"http://127.0.0.1:{cfg.server.port}/api/jukebox/spotify/callback"
        )
        self.spotify = SpotifyClient(http, self._on_tokens, self.jc.accounts_url, self.jc.api_url)
        self.device_name = self.jc.device_name
        self.guests: dict[str, dict[str, Any]] = {}
        self._by_hash: dict[str, str] = {}
        self.admin_id: str | None = None
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.queue: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.seq = 0
        self.account: str | None = None
        # Runtime only.
        self.current: dict[str, Any] | None = None  # queue entry we started
        self.now: dict[str, Any] | None = None
        self.votes: set[str] = set()
        self._votes_uri: str | None = None
        self.paused: str | None = None  # "admin" | "show"
        self._before_show: str | None = None
        self._resume_after_show = False
        self.device_id: str | None = None
        self.device_found = False
        self.error: str | None = None
        self.port80 = False
        self._pkce: dict[str, tuple[str, float]] = {}
        self._started_at = 0.0
        self._skipped_at = -1e9
        self.notice: str | None = None  # lasting warning (e.g. no Premium), shown under errors
        self._fallback_on = False
        self._foreign_uri: str | None = None
        self._transferred: str | None = None
        self._volume_sent: tuple[str, int] | None = None
        self._play_fails: dict[int, int] = {}
        self._lost = 0
        self._lost_msg: str | None = None
        self._cur_progress = 0  # last progress Spotify reported for our current track
        self._other_device = False  # last poll: playback sat on another (paused) device
        # Our last track's uri (or Spotify autoplay's follow-up), paused at the end so autoplay
        # can't play random songs; hidden from "now playing".
        self._ended_uri: str | None = None
        self._op = asyncio.Lock()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._sig: str | None = None
        self._pub_progress: tuple[int, float, bool] | None = None
        self._dirty = False

    # ----- persistence -----

    def load(self) -> None:
        with db.session() as s:
            row = s.get(Setting, KEY)
            data = dict(row.value) if row and isinstance(row.value, dict) else {}
        self.spotify.client_id = data.get("client_id") or None
        tok = data.get("tokens")
        self.spotify.tokens = tok if isinstance(tok, dict) and tok.get("refresh_token") else None
        self.device_name = data.get("device_name") or self.jc.device_name
        self.account = data.get("account") if self.spotify.tokens else None
        self.guests, self._by_hash = {}, {}
        for g in data.get("guests") or []:
            if isinstance(g, dict) and g.get("id"):
                # A restored backup has no token hashes: names stay, guests join again.
                self.guests[g["id"]] = {
                    "id": str(g["id"]),
                    "name": str(g.get("name") or "?"),
                    "token_hash": str(g.get("token_hash") or ""),
                    "banned": bool(g.get("banned")),
                    "last_seen": float(g.get("last_seen") or 0),
                }
                if g.get("token_hash"):
                    self._by_hash[g["token_hash"]] = g["id"]
        admin = data.get("admin_id")
        has_login = admin in self.guests and bool(self.guests[admin]["token_hash"])
        self.admin_id = admin if has_login else None
        saved = data.get("settings") or {}
        known = {k: saved[k] for k in DEFAULT_SETTINGS if k in saved}
        self.settings = {**DEFAULT_SETTINGS, **known}
        self.queue = [e for e in data.get("queue") or [] if isinstance(e, dict) and e.get("uri")]
        self.history = list(data.get("history") or [])[: self.HISTORY_MAX]
        cur = data.get("current")
        self.current = cur if isinstance(cur, dict) and cur.get("uri") else None
        ids = [int(e.get("id") or 0) for e in [*self.queue, self.current or {}]]
        ids.append(int(data.get("seq") or 0))
        self.seq = max(ids)

    def _save(self) -> None:
        data = {
            "client_id": self.spotify.client_id,
            "tokens": self.spotify.tokens,
            "device_name": self.device_name,
            "account": self.account,
            "guests": list(self.guests.values()),
            "admin_id": self.admin_id,
            "settings": self.settings,
            "queue": self.queue,
            "history": self.history,
            "current": self.current,
            "seq": self.seq,
        }
        with db.session() as s:
            row = s.get(Setting, KEY) or Setting(key=KEY, value={})
            row.value = data
            s.add(row)
            s.commit()
        self._dirty = False

    def _on_tokens(self, tokens: dict[str, Any] | None) -> None:
        if tokens is None:
            self.account = None
            self._set_error("Spotify login expired, connect again")
        try:
            self._save()
        except Exception:  # noqa: BLE001
            log.exception("jukebox: saving Spotify login failed")

    # ----- lifecycle -----

    async def start(self) -> None:
        self.load()
        self.port80 = await probe_port80()
        if self.jc.enabled:
            self._tasks = [
                asyncio.create_task(self._loop(), name="jukebox-loop"),
                asyncio.create_task(self._watch_bus(), name="jukebox-bus"),
            ]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._dirty:
            try:
                self._save()
            except Exception:  # noqa: BLE001
                log.exception("jukebox: final save failed")

    def wake(self) -> None:
        self._wake.set()

    # ----- state -----

    def _ref(self, gid: str | None) -> dict[str, str] | None:
        if gid is None:
            return None
        g = self.guests.get(gid)
        return {"id": gid, "name": g["name"] if g else "?"}

    def _entry_out(self, e: dict[str, Any]) -> dict[str, Any]:
        return {
            **{k: e.get(k) for k in ("uri", "title", "artist", "album", "art_url")},
            "duration_ms": int(e.get("duration_ms") or 0),
            "explicit": bool(e.get("explicit")),
            "id": e["id"],
            "guest": self._ref(e.get("guest_id")),
            "added_at": e.get("added_at") or 0,
            "bumped": e.get("bumped_seq") is not None,
        }

    def active_guests(self) -> int:
        cutoff = time.time() - self.ACTIVE_S
        return sum(1 for g in self.guests.values() if not g["banned"] and g["last_seen"] > cutoff)

    def votes_needed(self) -> int:
        return max(2, math.ceil(self.active_guests() * float(self.settings["skip_vote_ratio"])))

    def state(self) -> dict[str, Any]:
        now = None
        if self.now is not None:
            now = {**self.now, "votes": len(self.votes), "votes_needed": self.votes_needed()}
        return {
            "spotify": {
                "client_id_set": bool(self.spotify.client_id),
                "connected": self.spotify.connected,
                "account": self.account,
                "device_name": self.device_name,
                "device_found": self.device_found,
                "redirect_uri": self.redirect_uri,
                "error": self.error or self.notice,
            },
            "now": now,
            "queue": [self._entry_out(e) for e in ordered(self.queue)],
            "history": list(self.history),
            "paused": self.paused,
            "settings": dict(self.settings),
            "guests_active": self.active_guests(),
            "admin_id": self.admin_id,
            "port80": self.port80,
        }

    def _publish(self) -> None:
        """Push state when anything but the progress clock changed, or progress jumped."""
        st = self.state()
        now = st["now"]
        sig_now = {k: v for k, v in now.items() if k not in ("progress_ms", "at")} if now else None
        sig = json.dumps({**st, "now": sig_now}, sort_keys=True, default=str)
        jumped = False
        if now is not None and self._pub_progress is not None:
            p, at, playing = self._pub_progress
            expected = p + ((now["at"] - at) * 1000 if playing else 0)
            jumped = abs(now["progress_ms"] - expected) > 3000
        if sig != self._sig or jumped:
            self._sig = sig
            self._pub_progress = (now["progress_ms"], now["at"], now["is_playing"]) if now else None
            self.bus.publish("jukebox", st)

    def guests_list(self) -> list[dict[str, Any]]:
        pending: dict[str, int] = {}
        for e in self.queue:
            pending[e["guest_id"]] = pending.get(e["guest_id"], 0) + 1
        out = [
            {
                "id": g["id"],
                "name": g["name"],
                "banned": g["banned"],
                "last_seen": g["last_seen"],
                "pending": pending.get(g["id"], 0),
                "is_admin": g["id"] == self.admin_id,
            }
            for g in self.guests.values()
        ]
        out.sort(key=lambda g: g["last_seen"], reverse=True)
        return out

    def _publish_guests(self) -> None:
        self.bus.publish("jukebox_guests", self.guests_list())

    def _set_error(self, msg: str | None) -> None:
        if msg != self.error:
            if msg:
                log.warning("jukebox: %s", msg)
            self.error = msg
            self._publish()

    # ----- guests -----

    def guest_for(self, token: str | None, touch: bool = True) -> dict[str, Any] | None:
        if not token or len(token) > 200:
            return None
        g = self.guests.get(self._by_hash.get(_hash(token), ""))
        if g is not None and touch:
            was_active = g["last_seen"] > time.time() - self.ACTIVE_S
            g["last_seen"] = time.time()
            self._dirty = True
            if not was_active:
                self._publish()
        return g

    def is_admin(self, g: dict[str, Any]) -> bool:
        return g["id"] == self.admin_id

    def role(self, g: dict[str, Any]) -> str:
        return "admin" if self.is_admin(g) else "guest"

    def join(self, name: str, token: str | None) -> dict[str, Any]:
        name = " ".join(name.split())
        if not 1 <= len(name) <= 24:
            raise JukeboxError(422, "Name must be 1-24 characters")
        g = self.guest_for(token)
        if g is not None:
            g["name"] = name
            assert token is not None
            tok = token
        else:
            self._prune_guests()
            tok = secrets.token_urlsafe(24)
            gid = secrets.token_hex(4)
            while gid in self.guests:
                gid = secrets.token_hex(4)
            g = {
                "id": gid,
                "name": name,
                "token_hash": _hash(tok),
                "banned": False,
                "last_seen": time.time(),
            }
            self.guests[gid] = g
            self._by_hash[g["token_hash"]] = gid
        if self.admin_id is None and not g["banned"]:
            self.admin_id = g["id"]
        self._save()
        self._publish()
        self._publish_guests()
        return {"token": tok, "guest": self._ref(g["id"]), "role": self.role(g)}

    def _prune_guests(self) -> None:
        if len(self.guests) < self.GUESTS_MAX:
            return
        busy = {e["guest_id"] for e in self.queue}
        if self.current:
            busy.add(self.current.get("guest_id"))
        # Banned guests are kept so a ban sticks for the night.
        spare = [
            g
            for g in self.guests.values()
            if g["id"] not in busy and g["id"] != self.admin_id and not g["banned"]
        ]
        spare.sort(key=lambda g: g["last_seen"])
        for g in spare[: len(self.guests) - self.GUESTS_MAX + 1]:
            del self.guests[g["id"]]
            self._by_hash.pop(g["token_hash"], None)

    def reset_admin(self) -> None:
        self.admin_id = None
        self._save()
        self._publish()
        self._publish_guests()

    def make_admin(self, gid: str) -> None:
        g = self.guests.get(gid)
        if g is None:
            raise JukeboxError(404, "Unknown guest")
        if g["banned"]:
            raise JukeboxError(409, "Unban them first")
        self.admin_id = gid
        self._save()
        self._publish()
        self._publish_guests()

    def ban(self, gid: str, banned: bool) -> None:
        g = self.guests.get(gid)
        if g is None:
            raise JukeboxError(404, "Unknown guest")
        if banned and gid == self.admin_id:
            raise JukeboxError(409, "The host can't be banned")
        g["banned"] = banned
        if banned:
            self.queue = [e for e in self.queue if e["guest_id"] != gid]
        self._save()
        self._publish()
        self._publish_guests()

    # ----- queue -----

    def _pending(self, gid: str) -> int:
        return sum(1 for e in self.queue if e["guest_id"] == gid)

    def _check_add(self, g: dict[str, Any], uri: str) -> None:
        admin = self.is_admin(g)
        if not self.settings["accepting"] and not admin:
            raise JukeboxError(409, "The host closed the queue for now")
        if len(self.queue) >= self.QUEUE_MAX:
            raise JukeboxError(409, "The queue is full, try again later")
        if any(e["uri"] == uri for e in self.queue):
            raise JukeboxError(409, "That song is already in the queue")
        limit = int(self.settings["per_guest_limit"])
        if not admin and self._pending(g["id"]) >= limit:
            raise JukeboxError(
                409, f"You already have {limit} songs waiting. Add more once one has played."
            )

    def _require_spotify(self) -> None:
        if not self.spotify.connected:
            raise JukeboxError(503, "Spotify is not connected yet")

    def _api_error(self, exc: SpotifyError) -> JukeboxError:
        if exc.status in (400, 404):
            return JukeboxError(404, "Song not found on Spotify")
        if exc.status == 401:
            return JukeboxError(503, "Spotify is not connected")
        return JukeboxError(502, f"Spotify: {exc.message}")

    async def search(self, q: str) -> list[dict[str, Any]]:
        q = q.strip()
        if not 1 <= len(q) <= 200:
            raise JukeboxError(422, "Search must be 1-200 characters")
        self._require_spotify()
        tid = parse_track_id(q)
        try:
            results = [await self.spotify.track(tid)] if tid else await self.spotify.search(q, 10)
        except SpotifyError as exc:
            if tid and exc.status in (400, 404):
                return []
            raise self._api_error(exc) from exc
        pending = {e["uri"] for e in self.queue}
        return [{**t, "queued": t["uri"] in pending} for t in results]

    async def add(self, g: dict[str, Any], uri: str) -> dict[str, Any]:
        if g["banned"]:
            raise JukeboxError(403, "You can't add songs tonight")
        tid = parse_track_id(uri)
        if tid is None:
            raise JukeboxError(422, "That is not a Spotify song")
        uri = f"spotify:track:{tid}"
        self._check_add(g, uri)
        self._require_spotify()
        try:
            track = await self.spotify.track(tid)  # never trust client metadata
        except SpotifyError as exc:
            raise self._api_error(exc) from exc
        if track["explicit"] and not self.settings["allow_explicit"]:
            raise JukeboxError(422, "Explicit songs are switched off")
        max_min = int(self.settings["max_track_min"])
        if track["duration_ms"] > max_min * 60_000:
            raise JukeboxError(422, f"Songs longer than {max_min} min are not allowed")
        self._check_add(g, uri)  # again: another add may have landed during the lookup
        self.seq += 1
        entry = {
            **track,
            "uri": uri,
            "id": self.seq,
            "guest_id": g["id"],
            "added_seq": self.seq,
            "bumped_seq": None,
            "added_at": time.time(),
        }
        self.queue.append(entry)
        self._save()
        self._publish()
        self.wake()
        return self._entry_out(entry)

    def _find(self, entry_id: int) -> dict[str, Any]:
        e = next((e for e in self.queue if e["id"] == entry_id), None)
        if e is None:
            raise JukeboxError(404, "That song is no longer in the queue")
        return e

    def remove(self, g: dict[str, Any], entry_id: int) -> None:
        e = self._find(entry_id)
        if e["guest_id"] != g["id"] and not self.is_admin(g):
            raise JukeboxError(403, "You can only remove your own songs")
        self.queue.remove(e)
        self._save()
        self._publish()

    def bump(self, entry_id: int) -> None:
        e = self._find(entry_id)
        self.seq += 1
        e["bumped_seq"] = self.seq
        self._save()
        self._publish()

    def clear(self) -> None:
        self.queue = []
        self._save()
        self._publish()

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        new = {**self.settings, **{k: v for k, v in patch.items() if k in DEFAULT_SETTINGS}}
        if "fallback_uri" in patch:
            try:
                new["fallback_uri"] = normalize_context(patch["fallback_uri"])
            except ValueError as exc:
                raise JukeboxError(422, str(exc)) from exc
        volume_changed = new["volume"] != self.settings["volume"]
        self.settings = new
        self._save()
        self._publish()
        if volume_changed:
            spawn(self._apply_volume())
        self.wake()
        return dict(self.settings)

    async def _apply_volume(self) -> None:
        async with self._op:
            if not self.device_id or not self.spotify.connected:
                return
            try:
                await self._volume(self.device_id)
            except SpotifyError as exc:
                self._set_error(f"Volume: {exc.message}")

    # ----- skip votes and admin playback -----

    async def vote_skip(self, g: dict[str, Any]) -> dict[str, Any]:
        if g["banned"]:
            raise JukeboxError(403, "You can't vote tonight")
        if self.paused == "show":
            raise JukeboxError(409, "Paused for the show")
        if self.now is None:
            raise JukeboxError(409, "Nothing is playing")
        target = self.now["uri"]
        own = self.current is not None and self.current.get("guest_id") == g["id"]
        if not (own or self.is_admin(g)):
            self.votes.add(g["id"])
        votes, needed = len(self.votes), self.votes_needed()
        if own or self.is_admin(g) or votes >= needed:
            # Two votes crossing the threshold together must skip one track, not two.
            skipped = await self.skip(expect_uri=target)
            return {"ok": True, "skipped": skipped, "votes": votes, "votes_needed": needed}
        self._publish()
        return {"ok": True, "skipped": False, "votes": votes, "votes_needed": needed}

    async def skip(self, expect_uri: str | None = None) -> bool:
        """Skip the current track. False when it already changed (double tap, racing votes)."""
        async with self._op:
            if expect_uri is not None:
                if self.now is None or self.now.get("uri") != expect_uri:
                    return False
            elif time.monotonic() - self._skipped_at < self.SKIP_DEBOUNCE_S:
                return False
            if self.paused == "show":
                raise JukeboxError(409, "Paused for the show")
            self._require_spotify()
            try:
                dev = await self._need_device()
                self.votes.clear()
                if self.queue:
                    await self._advance(dev)
                elif self.settings["fallback_uri"]:
                    if self._fallback_on and self.current is None:
                        await self.spotify.next(dev)
                        self._started_at = time.monotonic()
                    else:
                        await self._play_fallback(dev)
                else:
                    await self._quiet_pause(dev)
                    self._ended_uri = self.now["uri"] if self.now else None
                    self.current = None
                    self.now = None
                    self._save()
                self.paused = None
                self._skipped_at = time.monotonic()
            except SpotifyError as exc:
                raise self._api_error(exc) from exc
            self._publish()
        self.wake()
        return True

    async def admin_pause(self) -> None:
        async with self._op:
            if self.paused == "show":
                self._before_show = "admin"
                self._resume_after_show = False
                return
            if self.device_id and self.spotify.connected:
                try:
                    await self._quiet_pause(self.device_id)
                except SpotifyError as exc:
                    raise self._api_error(exc) from exc
            self.paused = "admin"
            if self.now:
                self.now = {**self.now, "is_playing": False, "at": time.time()}
            self._publish()

    async def admin_resume(self) -> None:
        async with self._op:
            if self.paused == "show":
                if self._before_show == "admin":
                    self._before_show = None
                    self._resume_after_show = True
                return
            if self.paused == "admin":
                self.paused = None
                if self.device_id and self.spotify.connected and self.now is not None:
                    try:
                        await self._quiet_resume(self.device_id)
                    except SpotifyError as exc:
                        raise self._api_error(exc) from exc
                    self.now = {**self.now, "is_playing": True, "at": time.time()}
            self._publish()
        self.wake()

    async def _quiet_pause(self, dev: str) -> None:
        try:
            await self.spotify.pause(dev)
        except SpotifyError as exc:
            if exc.status != 403:  # 403 = already paused
                raise

    async def _quiet_resume(self, dev: str) -> None:
        try:
            await self.spotify.resume(dev)
        except SpotifyError as exc:
            if exc.status != 403:  # 403 = already playing
                raise

    # ----- audio output -----

    def write_audio_device(self, name: str | None) -> bool:
        """Store the mpv audio device for the music sink. True when the file changed."""
        path = self.jc.audio_device_file
        if path is None or not path.parent.is_dir():
            return False
        text = f"{name or 'auto'}\n"
        try:
            if path.read_text() == text:
                return False
        except OSError:
            pass
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text)
            tmp.chmod(0o644)  # raspotify runs as another (dynamic) user
            tmp.replace(path)
        except OSError as exc:
            log.warning("jukebox: audio device not written to %s: %s", path, exc)
            return False
        return True

    async def set_audio_device(self, name: str | None) -> None:
        if not self.write_audio_device(name):
            return
        # The sink reads the file when it starts, and librespot stops it on pause: pause and
        # resume move a playing song to the new output now.
        async with self._op:
            dev = self.device_id
            if not (dev and self.spotify.connected and self.paused is None):
                return
            if not (self.now and self.now.get("is_playing")):
                return
            try:
                await self._quiet_pause(dev)
                await asyncio.sleep(0.5)
                await self._quiet_resume(dev)
            except SpotifyError as exc:
                # Left paused: show it as a host pause so Resume is offered, not a silent stall.
                log.warning("jukebox: sink restart failed: %s", exc.message)
                self.paused = "admin"
                if self.now:
                    self.now = {**self.now, "is_playing": False, "at": time.time()}
                self._publish()

    # ----- Spotify setup -----

    def set_spotify(self, client_id: str | None, device_name: str | None) -> None:
        if client_id is not None:
            cid = client_id.strip()
            if cid and not _CLIENT_ID.match(cid):
                raise JukeboxError(422, "That does not look like a Spotify Client ID")
            if (cid or None) != self.spotify.client_id:
                self.spotify.client_id = cid or None
                # Tokens belong to the old app.
                self.spotify.tokens = None
                self.account = None
                self.notice = None
                self._reset_device()
        if device_name is not None:
            name = device_name.strip()
            if not 1 <= len(name) <= 64:
                raise JukeboxError(422, "Device name must be 1-64 characters")
            self.device_name = name
            self._reset_device()
        self._save()
        self._publish()
        self.wake()

    def _reset_device(self) -> None:
        self.device_id = None
        self.device_found = False
        self._transferred = None
        self._volume_sent = None

    def login_url(self) -> str:
        cid = self.spotify.client_id
        if not cid:
            raise JukeboxError(409, "Enter the Spotify Client ID first")
        url, state, verifier = self.spotify.auth_url(cid, self.redirect_uri)
        now = time.monotonic()
        self._pkce = {k: v for k, v in self._pkce.items() if now - v[1] < 900}
        while len(self._pkce) >= 10:
            self._pkce.pop(next(iter(self._pkce)))
        self._pkce[state] = (verifier, now)
        return url

    async def finish_login(self, code: str, state: str) -> None:
        pending = self._pkce.pop(state, None)
        if pending is None:
            raise JukeboxError(400, "Login expired or started elsewhere. Press Connect again.")
        try:
            await self.spotify.exchange(code, pending[0], self.redirect_uri)
            me = await self.spotify.me()
        except SpotifyError as exc:
            raise JukeboxError(502, f"Spotify login failed: {exc.message}") from exc
        self.account = str(me.get("display_name") or me.get("id") or "Spotify")
        self._reset_device()
        premium = me.get("product") in (None, "premium")
        self.error = None
        self.notice = None if premium else "Spotify Premium is needed to control playback"
        self._save()
        self._publish()
        self.wake()

    async def finish_from_url(self, url: str) -> None:
        qs = parse_qs(urlparse(url.strip()).query)
        if qs.get("error"):
            raise JukeboxError(400, f"Spotify said: {qs['error'][0]}")
        code, state = qs.get("code"), qs.get("state")
        if not code or not state:
            raise JukeboxError(422, "That address has no login code. Copy the whole address bar.")
        await self.finish_login(code[0], state[0])

    async def logout(self) -> None:
        async with self._op:
            if self.device_id and self.spotify.connected:
                try:  # the host expects the music to stop
                    await self._quiet_pause(self.device_id)
                except SpotifyError as exc:
                    log.info("jukebox: pause on logout failed: %s", exc.message)
        self.notice = None
        self.spotify.tokens = None
        self.account = None
        self.now = None
        self.error = None
        self._reset_device()
        self._save()
        self._publish()

    async def devices(self) -> list[dict[str, Any]]:
        self._require_spotify()
        try:
            devs = await self.spotify.devices()
        except SpotifyError as exc:
            raise self._api_error(exc) from exc
        return [
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "type": d.get("type"),
                "is_active": bool(d.get("is_active")),
            }
            for d in devs
        ]

    # ----- player loop -----

    def _busy(self) -> bool:
        try:
            return bool(self.show_busy())
        except Exception:  # noqa: BLE001
            log.exception("jukebox: show_busy failed")
            return False

    async def _watch_bus(self) -> None:
        q = self.bus.subscribe()
        try:
            while True:
                msg = await q.get()
                if msg.get("type") in ("show", "moment") and self._busy() != (
                    self.paused == "show"
                ):
                    self.wake()
        finally:
            self.bus.unsubscribe(q)

    async def _loop(self) -> None:
        backoff = 0.0
        while True:
            delay = self.jc.poll_idle_s
            try:
                async with self._op:
                    delay = await self._tick()
                backoff = 0.0
            except asyncio.CancelledError:
                raise
            except SpotifyError as exc:
                if exc.status == 404:
                    self._reset_device()
                self._set_error(exc.message)
                backoff = min(self.BACKOFF_MAX_S, max(self.BACKOFF_MIN_S, backoff * 2))
                delay = backoff
            except Exception:  # noqa: BLE001
                log.exception("jukebox: player loop error")
                self._set_error("Jukebox error, retrying")
                backoff = min(self.BACKOFF_MAX_S, max(self.BACKOFF_MIN_S, backoff * 2))
                delay = backoff
            if self._dirty:
                try:
                    self._save()
                except Exception:  # noqa: BLE001
                    log.exception("jukebox: save failed")
            try:
                await asyncio.wait_for(self._wake.wait(), max(0.01, delay))
            except TimeoutError:
                pass
            self._wake.clear()

    async def _find_device(self) -> str | None:
        want = self.device_name.casefold()
        devs = await self.spotify.devices()
        d = next((d for d in devs if str(d.get("name") or "").casefold() == want), None)
        self.device_found = d is not None and bool(d.get("id"))
        self.device_id = str(d["id"]) if d is not None and d.get("id") else None
        return self.device_id

    async def _need_device(self) -> str:
        dev = self.device_id or await self._find_device()
        if dev is None:
            self._publish()
            raise JukeboxError(503, f"Speaker '{self.device_name}' not found in Spotify")
        return dev

    def _ours(self, pb: dict[str, Any] | None) -> bool:
        dev = (pb or {}).get("device") or {}
        if self.device_id and dev.get("id") == self.device_id:
            return True
        return str(dev.get("name") or "").casefold() == self.device_name.casefold()

    async def _tick(self) -> float:
        """One poll + decision. Returns seconds until the next tick."""
        if not self.spotify.connected:
            self.now = None
            self._publish()
            return self.jc.poll_idle_s
        if not self.device_id:
            await self._find_device()
        pb = await self.spotify.playback()
        elsewhere: str | None = None
        if pb is not None and not self._ours(pb):
            # Someone listens on another device of the account: leave it alone while it plays;
            # a paused one may be taken over.
            if pb.get("is_playing"):
                elsewhere = str((pb.get("device") or {}).get("name") or "another device")
            pb = None
            self._other_device = True
        elif pb is not None:
            dev = pb.get("device") or {}
            if dev.get("id"):
                self.device_id, self.device_found = str(dev["id"]), True
        if pb is not None or elsewhere:
            self._other_device = elsewhere is not None
        self._absorb(pb)
        await self._apply_show(pb)
        playing = bool(pb and pb.get("is_playing"))
        delay = self.jc.poll_active_s if (playing or self.queue) else self.jc.poll_idle_s
        if self.paused is None and self.device_id and elsewhere is None:
            delay = await self._decide(pb, delay)
        if elsewhere:
            self.error = f"Spotify is playing on '{elsewhere}'; the queue waits"
        else:
            if not self.queue and self.current is None:
                self._lost = 0  # the requeued song was removed: nothing to retry
            self.error = self._lost_msg if self._lost else None
        self._publish()
        return delay

    def _absorb(self, pb: dict[str, Any] | None) -> None:
        item = (pb or {}).get("item")
        if not isinstance(item, dict) or not item.get("uri"):
            self.now = None
            return
        t = track_info(item)
        if t["uri"] == self._ended_uri and not (pb or {}).get("is_playing"):
            self.now = None
            return
        cur = self.current
        ours = cur is not None and t["uri"] == cur["uri"]
        if cur is not None and not ours and self._in_grace():
            return  # Spotify still reports the previous track
        if t["uri"] != self._votes_uri:
            self.votes.clear()
            self._votes_uri = t["uri"]
        assert pb is not None
        self.now = {
            **t,
            "progress_ms": int(pb.get("progress_ms") or 0),
            "at": time.time(),
            "is_playing": bool(pb.get("is_playing")),
            "entry_id": cur["id"] if ours and cur else None,
            "requested_by": self._ref(cur.get("guest_id")) if ours and cur else None,
        }

    def _in_grace(self) -> bool:
        return time.monotonic() - self._started_at < self.START_GRACE_S

    async def _apply_show(self, pb: dict[str, Any] | None) -> None:
        busy = self._busy()
        playing = bool(pb and pb.get("is_playing"))
        if busy:
            if self.paused != "show":
                self._before_show = self.paused
                self._resume_after_show = playing and self.paused is None
                self.paused = "show"
            if playing and self.device_id:
                await self._quiet_pause(self.device_id)  # retried each tick until it sticks
                if self.now:
                    self.now = {**self.now, "is_playing": False}
        elif self.paused == "show":
            self.paused = self._before_show
            resume, self._resume_after_show = self._resume_after_show, False
            self._before_show = None
            if resume and self.paused is None and self.device_id and not playing:
                await self._quiet_resume(self.device_id)
                if self.now:
                    self.now = {**self.now, "is_playing": True, "at": time.time()}
                self._started_at = time.monotonic()  # lag after resume looks like "stopped"

    async def _decide(self, pb: dict[str, Any] | None, delay: float) -> float:
        assert self.device_id is not None
        dev = self.device_id
        item = (pb or {}).get("item") or {}
        uri = item.get("uri")
        playing = bool(pb and pb.get("is_playing"))
        progress = int((pb or {}).get("progress_ms") or 0)
        left = int(item.get("duration_ms") or 0) - progress
        margin = self.END_MARGIN_MS

        if self.current is not None:
            self._foreign_uri = None
            if uri == self.current["uri"] and progress > 0:
                self._cur_progress = progress
            if uri != self.current["uri"]:
                if self._in_grace():
                    return min(delay, 0.5)
            elif left <= margin and not self._in_grace():
                pass  # ended; a late poll after the end may still report full progress
            elif not playing:
                if progress >= 1000 or self._in_grace():
                    return delay  # paused in the Spotify app: reflect, don't advance
            elif left > margin:
                if progress >= 10_000:
                    self._lost = 0  # the retry plays: the speaker is back
                return self._until_end(left, delay)
            if self._lost_speaker(uri, playing, progress):
                delay = self._requeue_lost()
                # librespot skips a song it can't load to the next one (autoplay) and fails
                # that too: stop it instead of letting it hammer Spotify for audio keys.
                if uri is not None and playing:
                    try:
                        await self._quiet_pause(dev)
                    except SpotifyError as exc:
                        log.info("jukebox: pause after failed track: %s", exc.message)
                return delay
            # Our track ended (or is in its last 300 ms).
            self._lost = 0
            dur = int(self.current.get("duration_ms") or 0)
            self.current = None
            self._dirty = True
            if self.queue:
                await self._advance(dev)
            elif self.settings["fallback_uri"]:
                await self._play_fallback(dev)
            elif uri is not None and self._cur_progress >= dur - 2 * self.NEAR_END_MS:
                # Our song ran out: Spotify autoplay would start "similar" random songs. A track
                # picked by hand in the Spotify app mid-song keeps playing.
                await self._quiet_pause(dev)
                self._ended_uri = uri
                self.now = None
            return delay

        prev, self._foreign_uri = self._foreign_uri, uri
        if self._fallback_on and self._in_grace():
            return min(delay, 0.5)
        if self.queue:
            # Never cut the fallback (or anything not ours) mid-song, playing or paused in the
            # Spotify app: wait for its end or a track change.
            changed = prev is not None and uri != prev
            stopped = not playing and (uri is None or progress < 1000 or uri == self._ended_uri)
            if stopped or left <= margin or changed:
                await self._advance(dev)
                return delay
            return self._until_end(left, delay)
        fb = self.settings["fallback_uri"]
        paused_mid = self._fallback_on and uri is not None and progress >= 1000
        if fb and not playing and not paused_mid:
            await self._play_fallback(dev)
        return delay

    def _lost_speaker(self, uri: str | None, playing: bool, progress: int) -> bool:
        cur = self.current
        assert cur is not None
        if self._other_device:
            return False  # moved to another device in the Spotify app: the user's choice
        gone = uri is None or (uri == cur["uri"] and not playing and progress < 1000)
        # Replaced before it ever got going: librespot couldn't load it ("Skipping to next track").
        gone = gone or (uri != cur["uri"] and self._cur_progress < self.LOST_PROGRESS_MS)
        # Spotify's progress, not wall time: show pauses and host pauses don't count.
        return gone and self._cur_progress < int(cur.get("duration_ms") or 0) / 2

    def _requeue_lost(self) -> float:
        cur = self.current
        assert cur is not None
        self.current = None
        self.now = None
        self.seq += 1
        self.queue.append({**cur, "bumped_seq": self.seq})
        if self.history and self.history[0].get("title") == cur.get("title"):
            self.history.pop(0)
        self._lost += 1
        self._dirty = True
        msg = (
            f"Speaker '{self.device_name}' couldn't play '{cur.get('title')}'. Check "
            "`journalctl -u raspotify` (audio key errors: Spotify refused the song) and "
            "Setup → Audio output; retrying"
        )
        self._lost_msg = msg
        self._set_error(msg)
        if self._lost == 1:
            self.bus.event("warn", f"Jukebox: {msg}")
        return min(self.BACKOFF_MAX_S, self.LOST_RETRY_S * self._lost)

    def _until_end(self, left_ms: int, delay: float) -> float:
        if left_ms < self.NEAR_END_MS:
            return max(0.05, (left_ms - self.END_MARGIN_MS) / 1000)
        return delay

    async def _prepare(self, dev: str) -> None:
        if self._transferred != dev:
            await self.spotify.transfer(dev)
            self._transferred = dev
        try:
            await self._volume(dev)
        except SpotifyError as exc:
            log.info("jukebox: volume not applied: %s", exc.message)

    async def _volume(self, dev: str) -> None:
        vol = int(self.settings["volume"])
        if self._volume_sent != (dev, vol):
            await self.spotify.volume(dev, vol)
            self._volume_sent = (dev, vol)

    async def _advance(self, dev: str) -> None:
        entry = ordered(self.queue)[0]
        await self._prepare(dev)
        try:
            await self.spotify.play(dev, uris=[entry["uri"]])
        except SpotifyError as exc:
            # A track Spotify refuses every time must not block the party.
            fails = self._play_fails.get(entry["id"], 0) + 1
            self._play_fails[entry["id"]] = fails
            track_refused = exc.status in (400, 403) and "premium" not in exc.message.lower()
            if fails >= self.PLAY_FAILS_MAX and track_refused:
                self._drop(entry["id"])
                self._play_fails.pop(entry["id"], None)
                self.bus.event("warn", f"Jukebox dropped '{entry['title']}': {exc.message}")
                self._save()
            raise
        self._play_fails.pop(entry["id"], None)
        # Removed by id: a guest may have removed it, or the host cleared, during the await.
        self._drop(entry["id"])
        self.current = entry
        self._started_at = time.monotonic()
        self._fallback_on = False
        self._foreign_uri = None
        self._ended_uri = None
        self._cur_progress = 0
        self.votes.clear()
        self._votes_uri = entry["uri"]
        self.now = {
            **{k: entry.get(k) for k in ("uri", "title", "artist", "album", "art_url")},
            "duration_ms": int(entry.get("duration_ms") or 0),
            "explicit": bool(entry.get("explicit")),
            "progress_ms": 0,
            "at": time.time(),
            "is_playing": True,
            "entry_id": entry["id"],
            "requested_by": self._ref(entry["guest_id"]),
        }
        self.history.insert(
            0,
            {
                "title": entry.get("title"),
                "artist": entry.get("artist"),
                "art_url": entry.get("art_url"),
                "guest": self._ref(entry["guest_id"]),
                "played_at": time.time(),
            },
        )
        del self.history[self.HISTORY_MAX :]
        self._save()
        self._publish()

    def _drop(self, entry_id: int) -> None:
        self.queue = [e for e in self.queue if e["id"] != entry_id]

    async def _play_fallback(self, dev: str) -> None:
        fb = self.settings["fallback_uri"]
        await self._prepare(dev)
        shuffled = True
        try:
            await self.spotify.shuffle(dev, True)
        except SpotifyError:
            shuffled = False
        await self.spotify.play(dev, context_uri=fb)
        if not shuffled:
            try:
                await self.spotify.shuffle(dev, True)
            except SpotifyError as exc:
                log.info("jukebox: shuffle not applied: %s", exc.message)
        self.current = None
        self._fallback_on = True
        self._foreign_uri = None
        self._ended_uri = None
        self._started_at = time.monotonic()
        self._dirty = True
