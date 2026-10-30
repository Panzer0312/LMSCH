"""Scene profiles: validation, export/import, engine cycle (mpv disabled), API + lock."""

import random

import httpx
import pytest
from asgi_lifespan import LifespanManager

from spookhub import db
from spookhub.main import create_app
from spookhub.models import Media
from tests.conftest import wait_for


@pytest.fixture
async def client(cfg):
    app = create_app(cfg)
    async with LifespanManager(app) as m:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=m.app), base_url="http://hub"
        ) as c:
            c.hub = app.state.hub  # type: ignore[attr-defined]
            yield c


def _media(tmp_path, name="v.mp4") -> int:
    f = tmp_path / name
    f.write_bytes(b"x")
    with db.session() as s:
        m = Media(filename=name, path=str(f), duration_s=5.0)
        s.add(m)
        s.commit()
        s.refresh(m)
        return m.id or 0


async def _profile(c, **body) -> int:
    r = await c.post("/api/profiles", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _scene(items, **kw):
    return {"items": [{"profile_id": i} for i in items], **kw}


async def test_scene_validation(client, tmp_path):
    c = client
    light = await _profile(c, name="L")
    r = await c.post("/api/profiles", json={"name": "S", "type": "scene"})
    assert r.status_code == 422  # scene type needs a scene
    r = await c.post("/api/profiles", json={"name": "S", "type": "scene", "scene": _scene([999])})
    assert r.status_code == 422
    r = await c.post(
        "/api/profiles", json={"name": "S", "type": "scene", "scene": _scene([light, light])}
    )
    assert r.status_code == 422
    sid = await _profile(c, name="S", type="scene", scene=_scene([light], idle_profile_id=light))
    out = (await c.get(f"/api/profiles/{sid}")).json()
    assert out["scene"] == {
        "items": [{"profile_id": light, "weight": 1.0}],
        "order": "shuffle",
        "idle_profile_id": light,
        "chain": False,
        "light_hold_s": 10.0,
    }
    # No nested scenes, neither as item nor as idle part, nor itself.
    r = await c.post("/api/profiles", json={"name": "S2", "type": "scene", "scene": _scene([sid])})
    assert r.status_code == 422
    r = await c.post(
        "/api/profiles",
        json={"name": "S2", "type": "scene", "scene": _scene([light], idle_profile_id=sid)},
    )
    assert r.status_code == 422
    r = await c.put(f"/api/profiles/{sid}", json={**out, "scene": _scene([sid])})
    assert r.status_code == 422
    # Other types never keep a scene.
    lid2 = await _profile(c, name="L2", scene=_scene([light]))
    assert (await c.get(f"/api/profiles/{lid2}")).json()["scene"] is None


async def test_delete_removes_from_scene(client):
    c = client
    a = await _profile(c, name="A")
    b = await _profile(c, name="B")
    sid = await _profile(c, name="S", type="scene", scene=_scene([a, b], idle_profile_id=b))
    assert (await c.post(f"/api/profiles/{sid}/activate")).status_code == 200
    assert (await c.delete(f"/api/profiles/{b}")).status_code == 200
    sc = (await c.get(f"/api/profiles/{sid}")).json()["scene"]
    assert [i["profile_id"] for i in sc["items"]] == [a]
    assert sc["idle_profile_id"] is None
    st = (await c.get("/api/scene")).json()
    assert st["profile_id"] == sid and set(st["upcoming"]) == {a}


async def test_export_import_by_name(client):
    c = client
    a = await _profile(c, name="Scare A")
    b = await _profile(c, name="Scare B")
    sid = await _profile(
        c, name="S", type="scene", scene=_scene([a, b], order="weighted", idle_profile_id=a)
    )
    data = (await c.get(f"/api/profiles/{sid}/export")).json()
    assert data["scene"]["items"] == [
        {"name": "Scare A", "weight": 1.0},
        {"name": "Scare B", "weight": 1.0},
    ]
    await c.delete(f"/api/profiles/{b}")
    r = await c.post("/api/profiles/import", json={"data": data})
    assert r.status_code == 201, r.text
    sc = r.json()["scene"]
    assert [i["profile_id"] for i in sc["items"]] == [a]  # missing one skipped
    assert sc["order"] == "weighted" and sc["idle_profile_id"] == a


async def test_scene_api_without_scene(client):
    c = client
    assert (await c.get("/api/scene")).json() == {
        "profile_id": None,
        "next_id": None,
        "pinned": None,
        "history": [],
        "upcoming": [],
    }
    assert (await c.post("/api/scene/skip")).status_code == 409
    assert (await c.post("/api/scene/pin", json={"profile_id": None})).status_code == 409


async def test_light_scene_cycle_pin_skip_and_lock(client):
    c = client
    hub = c.hub
    hub.engine.scene_rng = random.Random(1)
    idle = await _profile(c, name="Idle", base_state={"bri": 5})
    items = [await _profile(c, name=f"I{i}", base_state={"bri": 100 + i}) for i in range(3)]
    sid = await _profile(
        c,
        name="S",
        type="scene",
        cooldown_s=0,
        scene=_scene(items, order="sequential", idle_profile_id=idle, light_hold_s=0.2),
    )
    q = hub.bus.subscribe()
    await c.post(f"/api/profiles/{sid}/activate")
    eng = hub.engine
    assert eng.phase.value == "idle" and eng.active.id == sid
    assert eng.active.base.bri == 5
    assert eng.snapshot()["profile"]["type"] == "scene"
    st = (await c.get("/api/scene")).json()
    assert st["next_id"] == items[0] and st["upcoming"][:3] == items

    # Pin and skip are show-time actions: allowed under the lock.
    await c.post("/api/show_lock", json={"on": True})
    r = await c.post("/api/scene/pin", json={"profile_id": items[2]})
    assert r.status_code == 200 and r.json()["pinned"] == items[2]
    assert (await c.post("/api/scene/pin", json={"profile_id": idle})).status_code == 422
    r = await c.post("/api/show/trigger")
    assert r.json()["ok"], r.json()
    assert eng.active.id == items[2] and eng.active.base.bri == 102
    assert eng.snapshot()["profile"]["id"] == sid  # UI still sees the scene
    assert not (await c.post("/api/show/trigger")).json()["ok"]  # holding
    await wait_for(lambda: eng.active.id == sid, timeout=2)
    assert eng.phase.value == "idle" and eng.active.base.bri == 5

    r = await c.post("/api/scene/skip")
    assert r.status_code == 200
    st = r.json()
    assert st["history"] == [items[2]] and st["next_id"] == items[1]
    assert (await c.post("/api/show/trigger")).json()["ok"]
    assert eng.active.id == items[1]
    await wait_for(lambda: eng.active.id == sid, timeout=2)
    await c.post("/api/show_lock", json={"on": False})

    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    scene_msgs = [m for m in msgs if m["type"] == "scene"]
    assert scene_msgs and scene_msgs[-1]["data"]["history"] == [items[2], items[1]]
    hub.bus.unsubscribe(q)

    await eng.deactivate()
    assert (await c.get("/api/scene")).json()["profile_id"] is None


async def test_scene_cooldown_applies(client):
    c = client
    a = await _profile(c, name="A")
    sid = await _profile(
        c, name="S", type="scene", cooldown_s=60, scene=_scene([a], light_hold_s=0.1)
    )
    await c.post(f"/api/profiles/{sid}/activate")
    assert (await c.post("/api/show/trigger")).json()["ok"]
    await wait_for(lambda: c.hub.engine.active.id == sid, timeout=2)
    r = (await c.post("/api/show/trigger")).json()
    assert not r["ok"] and "cooldown" in r["message"]


async def test_video_scene_cycle_and_chain(client, tmp_path):
    """mpv disabled: the end of an item is simulated; checks the state machine only."""
    c = client
    eng = c.hub.engine
    mid = _media(tmp_path)
    idle_mid = _media(tmp_path, "idle.mp4")
    idle = await _profile(
        c, name="Fog", type="video", media_id=mid, idle_media_id=idle_mid, base_state={"bri": 7}
    )
    v1 = await _profile(c, name="V1", type="video", media_id=mid, playback_mode="loop")
    v2 = await _profile(c, name="V2", type="video", media_id=mid)
    sid = await _profile(
        c, name="S", type="scene", scene=_scene([v1, v2], order="sequential", idle_profile_id=idle)
    )
    await c.post(f"/api/profiles/{sid}/activate")
    assert eng.phase.value == "idle"
    assert eng.active.idle_path and eng.active.idle_path.endswith("idle.mp4")
    assert (await c.post("/api/show/trigger")).json()["ok"]
    assert eng.playing_main and eng.active.id == v1
    assert eng.active.mode.value == "once"  # items play once, whatever their own mode
    await eng._finish("ended")
    assert not eng.playing_main and eng.active.id == sid and eng.phase.value == "idle"

    # Chain: back to back, no trigger.
    body = (await c.get(f"/api/profiles/{sid}")).json()
    body["scene"]["chain"] = True
    assert (await c.put(f"/api/profiles/{sid}", json=body)).status_code == 200
    await c.post(f"/api/profiles/{sid}/activate")
    await wait_for(lambda: eng.playing_main, timeout=2)
    first = eng.active.id
    await eng._finish("ended")
    await wait_for(lambda: eng.playing_main and eng.active.id != first, timeout=2)
    # A manual stop pauses the chain.
    await eng.stop_show()
    assert not eng.playing_main and eng.active.id == sid
    # Blackout mid-item comes back to the scene, not the item.
    assert (await c.post("/api/show/trigger")).json()["ok"]
    await eng.set_blackout(True)
    hist = eng.scene_state()["history"]
    await eng.set_blackout(False)
    assert eng.home_id() == sid and eng.scene_state()["history"] == hist


def test_scene_migration_up_and_down(tmp_path):
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    db_file = tmp_path / "m.db"
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "b2c3e4f5a602")
    con = sqlite3.connect(db_file)
    # The profiletype enum has no CHECK constraint, so 'scene' needs no table rebuild.
    con.execute(
        "INSERT INTO profile (name, type, sort, selection, base_state, overrides, "
        """playback_mode, cooldown_s, scene) VALUES ('s','scene',0,'{}','{}','{}','once',0,"""
        """'{"items": []}')"""
    )
    con.commit()
    con.close()
    command.downgrade(cfg, "b1a0d4e5c601")
    con = sqlite3.connect(db_file)
    assert "scene" not in {r[1] for r in con.execute("PRAGMA table_info(profile)")}
    con.close()
