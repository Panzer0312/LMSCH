from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from spookhub.config import JukeboxCfg
from spookhub.main import create_app
from tests.conftest import wait_for
from tests.jukebox_fake import FakeSpotify, tid


@pytest.fixture
async def client(cfg):
    cfg.jukebox = JukeboxCfg(poll_active_s=0.05, poll_idle_s=0.1)
    app = create_app(cfg)
    fake = FakeSpotify()
    jb = app.state.hub.jukebox
    jb.spotify.http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    jb.START_GRACE_S = 0.0
    jb.SKIP_DEBOUNCE_S = 0.0
    async with LifespanManager(app) as m:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=m.app), base_url="http://hub"
        ) as c:
            c.hub = app.state.hub  # type: ignore[attr-defined]
            c.fake = fake  # type: ignore[attr-defined]
            yield c
    await jb.spotify.http.aclose()


def tok(t: str) -> dict[str, str]:
    return {"X-Jukebox-Token": t}


async def connect_spotify(client, host: str) -> None:
    r = await client.put(
        "/api/jukebox/admin/spotify", json={"client_id": "b" * 32}, headers=tok(host)
    )
    assert r.json() == {"ok": True}
    url = (await client.get("/api/jukebox/admin/spotify/login", headers=tok(host))).json()["url"]
    state = httpx.URL(url).params["state"]
    r = await client.get(f"/api/jukebox/spotify/callback?code=c&state={state}")
    assert r.status_code == 302 and r.headers["location"] == "/music?spotify=ok"


async def test_guest_flow(client) -> None:
    assert (await client.get("/api/jukebox/me")).status_code == 401
    host = (await client.post("/api/jukebox/join", json={"name": "Host"})).json()
    assert host["role"] == "admin"
    bob = (await client.post("/api/jukebox/join", json={"name": "Bob"})).json()
    assert bob["role"] == "guest"
    me = (await client.get("/api/jukebox/me", headers=tok(bob["token"]))).json()
    assert me == {"guest": bob["guest"], "role": "guest"}
    r = await client.post("/api/jukebox/admin/skip", headers=tok(bob["token"]))
    assert r.status_code == 403
    r = await client.get("/api/jukebox/search?q=song", headers=tok(bob["token"]))
    assert r.status_code == 503
    r = await client.get("/api/jukebox/spotify/callback?code=c&state=forged")
    assert r.headers["location"] == "/music?spotify=error"

    await connect_spotify(client, host["token"])
    # Hold the player so the queue stays put while we look at it.
    r = await client.post("/api/jukebox/admin/pause", headers=tok(host["token"]))
    assert r.json() == {"ok": True}
    r = await client.get("/api/jukebox/search?q=song 2", headers=tok(bob["token"]))
    assert [t["title"] for t in r.json()] == ["Song 2"] and r.json()[0]["queued"] is False
    r = await client.post(
        "/api/jukebox/queue", json={"uri": f"spotify:track:{tid(2)}"}, headers=tok(bob["token"])
    )
    assert r.status_code == 200 and r.json()["guest"]["name"] == "Bob"
    r = await client.post(
        "/api/jukebox/queue", json={"uri": f"spotify:track:{tid(2)}"}, headers=tok(bob["token"])
    )
    assert r.status_code == 409 and "already" in r.json()["detail"]
    assert len((await client.get("/api/jukebox/state")).json()["queue"]) == 1
    await client.post("/api/jukebox/admin/resume", headers=tok(host["token"]))
    fake = client.fake
    await wait_for(lambda: bool(fake.play_calls()))
    st = (await client.get("/api/jukebox/state")).json()
    assert st["now"]["title"] == "Song 2" and st["spotify"]["account"] == "Host"
    assert st["admin_id"] == host["guest"]["id"] and isinstance(st["port80"], bool)

    r = await client.put(
        "/api/jukebox/admin/settings",
        json={"per_guest_limit": 5, "fallback_uri": "nope"},
        headers=tok(host["token"]),
    )
    assert r.status_code == 422
    r = await client.put(
        "/api/jukebox/admin/settings", json={"volume": 40}, headers=tok(host["token"])
    )
    assert r.json()["volume"] == 40 and r.json()["per_guest_limit"] == 3
    guests = (await client.get("/api/jukebox/admin/guests", headers=tok(host["token"]))).json()
    assert {g["name"] for g in guests} == {"Host", "Bob"}
    assert all("token_hash" not in g for g in guests)
    devs = (
        await client.get("/api/jukebox/admin/spotify/devices", headers=tok(host["token"]))
    ).json()
    assert devs[0]["name"] == "Spookhub"


async def test_pin_does_not_guard_jukebox_but_reset(client) -> None:
    await client.put("/api/auth/pin", json={"pin": "1234"})
    client.cookies.clear()
    assert (await client.get("/api/state")).status_code == 401
    assert (await client.get("/api/jukebox/state")).status_code == 200
    j = await client.post("/api/jukebox/join", json={"name": "Host"})
    assert j.status_code == 200
    assert (await client.post("/api/jukebox_reset")).status_code == 401
    await client.post("/api/auth/login", json={"pin": "1234"})
    assert (await client.post("/api/jukebox_reset")).json() == {"ok": True}
    assert client.hub.jukebox.admin_id is None
    r = await client.post(
        "/api/jukebox/join", json={"name": "Host"}, headers=tok(j.json()["token"])
    )
    assert r.json()["role"] == "admin"


async def test_show_lock_allows_jukebox(client) -> None:
    await client.post("/api/show_lock", json={"on": True})
    j = await client.post("/api/jukebox/join", json={"name": "Host"})
    assert j.status_code == 200
    r = await client.post(
        "/api/jukebox/queue",
        json={"uri": f"spotify:track:{tid(1)}"},
        headers=tok(j.json()["token"]),
    )
    assert r.status_code == 503  # passed the lock; Spotify is just not connected
    assert (await client.post("/api/jukebox_reset")).status_code == 423


async def test_ws_jukebox_snapshot(cfg) -> None:
    from starlette.testclient import TestClient

    with TestClient(create_app(cfg)) as tc:
        host = tc.post("/api/jukebox/join", json={"name": "Host"}).json()
        with tc.websocket_connect(f"/ws/jukebox?token={host['token']}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "snapshot"
            assert msg["data"]["jukebox"]["admin_id"] == host["guest"]["id"]
            tc.post("/api/jukebox/join", json={"name": "Bob"})
            types = {ws.receive_json()["type"], ws.receive_json()["type"]}
            assert types == {"jukebox", "jukebox_guests"}
        with tc.websocket_connect("/ws/jukebox") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            tc.post("/api/jukebox/join", json={"name": "Eve"})
            # Guests (no admin token) only get jukebox state, never the guest list.
            assert ws.receive_json()["type"] == "jukebox"
        with tc.websocket_connect("/ws") as ws:
            assert "jukebox" in ws.receive_json()["data"]


def test_ws_token_redacted_from_logs() -> None:
    import logging

    from spookhub.main import _RedactTokens

    args = ("1.2.3.4", "/ws/jukebox?token=s3cr-t&code=c0de&state=st4te")
    rec = logging.LogRecord("uvicorn.error", 20, "", 0, '%s - "WebSocket %s"', args, None)
    _RedactTokens().filter(rec)
    msg = rec.getMessage()
    assert all(x not in msg for x in ("s3cr-t", "c0de", "st4te"))
    assert "token=***" in msg and "code=***" in msg
