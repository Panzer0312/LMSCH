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
    r = await client.post("/api/boards/aa/identify")
    assert r.status_code == 404  # reached the route (unknown board), not the lock
    r = await client.post("/api/boards/aa/state", json={"on": True})
    assert r.status_code == 404
    assert (await client.post("/api/setup/test_sound")).status_code == 423
    assert (await client.post("/api/show/test_pattern", json={"on": True})).status_code == 423
    assert (await client.put("/api/auth/pin", json={"pin": "1"})).status_code == 423
    assert (await client.put("/api/auth/trigger_token", json={"token": "x"})).status_code == 423
    assert (await client.post("/api/auth/login", json={"pin": "x"})).status_code == 200
    assert (await client.post("/api/auth/logout")).status_code == 200
    assert (await client.post("/api/show/stop")).status_code == 200
    assert (await client.post("/api/showX/stop")).status_code == 423
    assert (await client.post("/api/blackout/", json={"on": False})).status_code != 423
    r = await client.put("/api/settings", content=b"{nope", headers={"content-type": "x/y"})
    assert r.status_code == 423
    r = await client.put("/api/settings", json={"master_volume": 0.5, "pad": "x" * 5000})
    assert r.status_code == 413
    r = await client.put("/api/settings", json={"show_lock": False, "master_volume": 0.4})
    assert r.status_code == 423
    assert client.hub.settings.value.show_lock
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


async def test_second_hub_sees_persisted_events(cfg):
    from spookhub.hub import Hub

    h1 = Hub(cfg)
    await h1.start()
    h1.bus.event("info", "before restart", kind="board")
    await h1.stop()
    h2 = Hub(cfg)
    await h2.start()
    try:
        with db.session() as s:
            msgs = [e.message for e in s.exec(select(EventLog))]
        assert "before restart" in msgs
    finally:
        await h2.stop()


async def test_background_flush_prune_and_requeue(cfg, monkeypatch):
    import asyncio

    from spookhub import events as ev

    db.init_engine(cfg.paths.db_path)
    bus = EventBus()
    w = EventWriter(bus, flush_s=0.05, prune_s=0.05)
    with db.session() as s:
        s.add(EventLog(ts=time.time() - 8 * 86400, level="info", kind="info", message="old"))
        s.commit()
    await w.start()
    bus.event("info", "fresh", odd=object())  # non-JSON extra must not poison the batch
    for _ in range(100):
        await asyncio.sleep(0.05)
        with db.session() as s:
            msgs = {e.message for e in s.exec(select(EventLog))}
        if "fresh" in msgs:
            break
    assert "fresh" in msgs and "old" not in msgs

    real = ev._write
    calls = {"n": 0}

    def flaky(rows):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")
        real(rows)

    monkeypatch.setattr(ev, "_write", flaky)
    bus.event("info", "retry-me")
    await asyncio.sleep(0.4)  # first flush fails, loop retries
    await w.stop()
    with db.session() as s:
        assert s.exec(select(EventLog).where(EventLog.message == "retry-me")).first()
    assert calls["n"] >= 2


async def test_requeue_keeps_newest(cfg, monkeypatch):
    from spookhub import events as ev

    db.init_engine(cfg.paths.db_path)
    monkeypatch.setattr(ev, "PENDING_MAX", 3)
    bus = EventBus()
    w = EventWriter(bus)
    w._requeue([{"message": str(i)} for i in range(5)])
    assert [e["message"] for e in bus.pending] == ["2", "3", "4"]


async def test_stats_ignore_refused_triggers(client, tmp_path):
    from spookhub.models import Media
    from tests.test_setup import FakePlayer

    client.hub.engine.player = FakePlayer()
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    with db.session() as s:
        m = Media(filename="v.mp4", path=str(f), duration_s=5)
        s.add(m)
        s.commit()
        s.refresh(m)
    r = await client.post(
        "/api/profiles",
        json={"name": "V", "type": "video", "media_id": m.id, "idle_media_id": m.id,
              "playback_mode": "idle_trigger", "cooldown_s": 60, "base_state": {"on": True}},
    )  # fmt: skip
    await client.hub.engine.activate(r.json()["id"])
    eng = client.hub.engine
    assert (await eng.trigger("button"))[0] is True
    assert (await eng.trigger("button"))[0] is False  # refused: not counted
    s = (await client.get("/api/events/stats")).json()
    assert s["triggers"] == 1
