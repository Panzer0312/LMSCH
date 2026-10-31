"""Jukebox: open to guests (no PIN, allowed in Show mode). Guests prove identity with the
X-Jukebox-Token header; the first guest to join is the admin (host)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.jukebox.player_page import PAGE
from spookhub.jukebox.service import JukeboxError, JukeboxService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jukebox", tags=["jukebox"])
# Not under the open /api/jukebox/ prefix: reset needs the normal Spookhub auth, and the
# WebSocket lives next to /ws.
extra_router = APIRouter(tags=["jukebox"])
HubDep = Annotated[Hub, Depends(get_hub)]
# Every handler and dependency here is async on purpose: the service publishes to the event bus
# (asyncio queues), which must not happen from FastAPI's threadpool.


async def get_jukebox(hub: HubDep) -> JukeboxService:
    return hub.jukebox


JukeDep = Annotated[JukeboxService, Depends(get_jukebox)]


async def get_guest(
    jb: JukeDep, x_jukebox_token: Annotated[str | None, Header()] = None
) -> dict[str, Any]:
    g = jb.guest_for(x_jukebox_token)
    if g is None:
        raise HTTPException(401, "join first")
    return g


GuestDep = Annotated[dict[str, Any], Depends(get_guest)]


async def get_admin(jb: JukeDep, g: GuestDep) -> dict[str, Any]:
    if not jb.is_admin(g):
        raise HTTPException(403, "Only the host can do that")
    return g


AdminDep = Annotated[dict[str, Any], Depends(get_admin)]


async def get_player(jb: JukeDep, x_player_key: Annotated[str | None, Header()] = None) -> None:
    if not jb.player_auth(x_player_key):
        raise HTTPException(403, "player key required")


PlayerDep = Annotated[None, Depends(get_player)]
OK = {"ok": True}


class JoinIn(BaseModel):
    name: str = Field(max_length=200)


class AddIn(BaseModel):
    uri: str = Field(min_length=1, max_length=300)


class BanIn(BaseModel):
    banned: bool = True


class SettingsPatch(BaseModel):
    accepting: bool | None = None
    per_guest_limit: int | None = Field(default=None, ge=1, le=20)
    allow_explicit: bool | None = None
    fallback_uri: str | None = Field(default=None, max_length=300)
    volume: int | None = Field(default=None, ge=0, le=100)
    skip_vote_ratio: float | None = Field(default=None, ge=0.1, le=1.0)
    max_track_min: int | None = Field(default=None, ge=1, le=30)


class SpotifyIn(BaseModel):
    client_id: str | None = Field(default=None, max_length=100)
    device_name: str | None = Field(default=None, max_length=100)


class CodeIn(BaseModel):
    url: str = Field(min_length=1, max_length=4000)


# ----- guests -----


@router.post("/join")
async def join(
    body: JoinIn, jb: JukeDep, x_jukebox_token: Annotated[str | None, Header()] = None
) -> dict[str, Any]:
    return jb.join(body.name, x_jukebox_token)


@router.get("/me")
async def me(jb: JukeDep, g: GuestDep) -> dict[str, Any]:
    return {"guest": {"id": g["id"], "name": g["name"]}, "role": jb.role(g)}


@router.get("/state")
async def state(jb: JukeDep) -> dict[str, Any]:
    return jb.state()


@router.get("/search")
async def search(
    jb: JukeDep, g: GuestDep, q: Annotated[str, Query(min_length=1, max_length=200)]
) -> list[dict[str, Any]]:
    return await jb.search(q)


@router.post("/queue")
async def add(body: AddIn, jb: JukeDep, g: GuestDep) -> dict[str, Any]:
    return await jb.add(g, body.uri)


@router.delete("/queue/{entry_id}")
async def remove(entry_id: int, jb: JukeDep, g: GuestDep) -> dict[str, bool]:
    jb.remove(g, entry_id)
    return OK


@router.post("/vote_skip")
async def vote_skip(jb: JukeDep, g: GuestDep) -> dict[str, Any]:
    return await jb.vote_skip(g)


# ----- host -----


class SkipIn(BaseModel):
    uri: str | None = Field(default=None, max_length=300)


@router.post("/admin/skip")
async def admin_skip(jb: JukeDep, _: AdminDep, body: SkipIn | None = None) -> dict[str, bool]:
    """Optional {uri}: skip only if that track still plays (double-tap safe)."""
    await jb.skip(expect_uri=body.uri if body else None)
    return OK


@router.post("/admin/pause")
async def admin_pause(jb: JukeDep, _: AdminDep) -> dict[str, bool]:
    await jb.admin_pause()
    return OK


@router.post("/admin/resume")
async def admin_resume(jb: JukeDep, _: AdminDep) -> dict[str, bool]:
    await jb.admin_resume()
    return OK


@router.post("/admin/clear")
async def admin_clear(jb: JukeDep, _: AdminDep) -> dict[str, bool]:
    jb.clear()
    return OK


@router.post("/admin/queue/{entry_id}/bump")
async def admin_bump(entry_id: int, jb: JukeDep, _: AdminDep) -> dict[str, bool]:
    jb.bump(entry_id)
    return OK


@router.put("/admin/settings")
async def admin_settings(body: SettingsPatch, jb: JukeDep, _: AdminDep) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    # null means "unchanged" for everything but the fallback (null clears it).
    patch = {k: v for k, v in patch.items() if v is not None or k == "fallback_uri"}
    return jb.update_settings(patch)


@router.get("/admin/guests")
async def admin_guests(jb: JukeDep, _: AdminDep) -> list[dict[str, Any]]:
    return jb.guests_list()


@router.post("/admin/guests/{gid}/ban")
async def admin_ban(gid: str, body: BanIn, jb: JukeDep, _: AdminDep) -> dict[str, bool]:
    jb.ban(gid, body.banned)
    return OK


@router.post("/admin/guests/{gid}/make_admin")
async def admin_make_admin(gid: str, jb: JukeDep, _: AdminDep) -> dict[str, bool]:
    jb.make_admin(gid)
    return OK


@router.put("/admin/spotify")
async def admin_spotify(body: SpotifyIn, jb: JukeDep, _: AdminDep) -> dict[str, bool]:
    jb.set_spotify(body.client_id, body.device_name)
    return OK


@router.get("/admin/spotify/login")
async def admin_spotify_login(jb: JukeDep, _: AdminDep) -> dict[str, str]:
    return {"url": jb.login_url()}


@router.post("/admin/spotify/code")
async def admin_spotify_code(body: CodeIn, jb: JukeDep, _: AdminDep) -> dict[str, bool]:
    await jb.finish_from_url(body.url)
    return OK


@router.post("/admin/spotify/logout")
async def admin_spotify_logout(jb: JukeDep, _: AdminDep) -> dict[str, bool]:
    await jb.logout()
    return OK


@router.get("/admin/spotify/devices")
async def admin_spotify_devices(jb: JukeDep, _: AdminDep) -> list[dict[str, Any]]:
    return await jb.devices()


@router.get("/spotify/callback", include_in_schema=False)
async def spotify_callback(
    jb: JukeDep,
    code: Annotated[str | None, Query(max_length=2000)] = None,
    state: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    # Only works when the admin browser runs on the hub (loopback redirect).
    if code and state:
        try:
            await jb.finish_login(code, state)
            return RedirectResponse("/music?spotify=ok", status_code=302)
        except JukeboxError as exc:
            log.warning("jukebox: Spotify callback failed: %s", exc.detail)
    return RedirectResponse("/music?spotify=error", status_code=302)


@router.get("/player", include_in_schema=False)
async def player_page() -> HTMLResponse:
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


@router.get("/player/token")
async def player_token(jb: JukeDep, _: PlayerDep) -> dict[str, str]:
    return await jb.player_token()


class PlayerStatusIn(BaseModel):
    error: str | None = Field(default=None, max_length=1000)


@router.post("/player/status")
async def player_status(body: PlayerStatusIn, jb: JukeDep, _: PlayerDep) -> dict[str, bool]:
    jb.player_status(body.error)
    return OK


# ----- outside the open prefix -----


@extra_router.post("/api/jukebox_reset")
async def jukebox_reset(jb: JukeDep) -> dict[str, bool]:
    """Clear the jukebox admin; the next guest to join takes it."""
    jb.reset_admin()
    return OK


@extra_router.websocket("/ws/jukebox")
async def ws_jukebox(websocket: WebSocket, token: str | None = None) -> None:
    hub: Hub = websocket.app.state.hub
    jb = hub.jukebox
    await websocket.accept()
    jb.guest_for(token)  # counts as activity for skip votes
    q = hub.bus.subscribe()
    try:
        await websocket.send_json({"type": "snapshot", "data": {"jukebox": jb.state()}})

        async def drain_incoming() -> None:
            while True:
                await websocket.receive_text()  # pings only

        reader = asyncio.create_task(drain_incoming())
        try:
            while True:
                getter = asyncio.create_task(q.get())
                done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    getter.cancel()
                    break
                msg = getter.result()
                if msg["type"] == "jukebox":
                    await websocket.send_json(msg)
                elif msg["type"] == "jukebox_guests":
                    # The guest list is for the host only.
                    g = jb.guest_for(token, touch=False)
                    if g is not None and jb.is_admin(g):
                        await websocket.send_json(msg)
        finally:
            reader.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.bus.unsubscribe(q)
        with contextlib.suppress(Exception):
            await websocket.close()
