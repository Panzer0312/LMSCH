import time

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlmodel import select

from spookhub import db
from spookhub.events import EventBus, EventWriter
from spookhub.main import create_app
from spookhub.models import EventLog


@pytest.fixture
async def client(cfg):
    app = create_app(cfg)
    async with LifespanManager(app) as m:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=m.app), base_url="http://hub"
        ) as c:
            c.hub = app.state.hub  # type: ignore[attr-defined]
            yield c


async def test_lock_blocks_edits_allows_show_actions(client):
    pid = (await client.post("/api/profiles", json={"name": "P"})).json()["id"]
    assert (await client.post("/api/show_lock", json={"on": True})).json() == {"show_lock": True}
    assert client.hub.settings.value.show_lock
    r = await client.put(f"/api/profiles/{pid}/cues", json=[])
    assert r.status_code == 423 and r.json() == {"detail": "Show mode is on"}
    assert (await client.post("/api/trigger")).status_code == 200
    assert (await client.post("/api/blackout", json={"on": True})).status_code == 200
    assert (await client.post("/api/blackout", json={"on": False})).status_code == 200
    assert (await client.post("/api/boards/aa/identify")).status_code != 423
    assert (await client.post("/api/boards/aa/state", json={})).status_code != 423
    assert (await client.post("/api/setup/test_sound")).status_code == 423
    assert (await client.put("/api/settings", json={"master_volume": 0.5})).status_code == 200
    r = await client.put("/api/settings", json={"master_volume": 0.5, "sync_offset_ms": 5})
    assert r.status_code == 423
    assert (await client.get("/api/profiles")).status_code == 200
    # PUT /api/settings can never flip the lock.
    await client.post("/api/show_lock", json={"on": False})
    assert not client.hub.settings.value.show_lock


async def test_unlock_needs_pin(client):
    await client.put("/api/auth/pin", json={"pin": "1234"})  # keeps cookie
    await client.post("/api/show_lock", json={"on": True})
    assert (await client.post("/api/show_lock", json={"on": False})).status_code == 401
    r = await client.post("/api/show_lock", json={"on": False, "pin": "9"})
    assert r.status_code == 401
    assert client.hub.settings.value.show_lock
    r = await client.post("/api/show_lock", json={"on": False, "pin": "1234"})
    assert r.status_code == 200 and not client.hub.settings.value.show_lock


async def test_auth_beats_lock(client):
    await client.put("/api/auth/pin", json={"pin": "1234"})
    await client.post("/api/show_lock", json={"on": True})
    client.cookies.clear()
    assert (await client.put("/api/profiles/1/cues", json=[])).status_code == 401


async def test_events_persist_and_survive_new_bus(client):
    bus = client.hub.bus
    bus.event("info", "hello", kind="board", mac="aa")
    bus.event("info", "plain")
    r = await client.get("/api/events", params={"kind": "board"})
    rows = r.json()
    assert [e["message"] for e in rows] == ["hello"] and rows[0]["data"] == {"mac": "aa"}
    # A fresh bus/writer (restart) still sees the rows.
    w = EventWriter(EventBus())
    await w.flush()
    allrows = (await client.get("/api/events", params={"limit": 2})).json()
    assert len(allrows) == 2 and allrows[0]["ts"] >= allrows[1]["ts"]
    assert (await client.get("/api/events", params={"limit": 5000})).status_code == 422


async def test_stats_counts_triggers(client):
    bus = client.hub.bus
    for _ in range(3):
        bus.event("info", "Triggered (button)", kind="trigger")
    bus.event("info", "other")
    s = (await client.get("/api/events/stats")).json()
    assert s["triggers"] == 3
    assert sum(b["count"] for b in s["by_hour"]) == 3
    assert (await client.get("/api/events/stats", params={"since": time.time() + 60})).json()[
        "triggers"
    ] == 0


async def test_retention_and_background_flush(client):
    old = time.time() - 8 * 86400
    with db.session() as s:
        s.add(EventLog(ts=old, level="info", kind="info", message="old"))
        s.commit()
    await client.hub.events.prune()
    with db.session() as s:
        assert s.exec(select(EventLog).where(EventLog.message == "old")).first() is None
    client.hub.bus.event("info", "bg")
    await client.hub.events.stop()  # flushes on stop
    with db.session() as s:
        assert s.exec(select(EventLog).where(EventLog.message == "bg")).first() is not None
