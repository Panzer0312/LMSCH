"""Moments (4.5): CRUD + validation, lock, cooldown, busy policies, variant pick, run + restore."""

import asyncio
import random
import sqlite3
import time

import httpx
import pytest
from alembic import command
from asgi_lifespan import LifespanManager

from spookhub import db
from spookhub.main import create_app
from spookhub.models import Media, MomentIn
from spookhub.show.moments import MomentRunner
from tests.conftest import wait_for
from tests.test_migrations import _cfg
from tests.test_sound import FakeFx


@pytest.fixture
async def client(cfg):
    app = create_app(cfg)
    async with LifespanManager(app) as m:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=m.app), base_url="http://hub"
        ) as c:
            c.hub = app.state.hub  # type: ignore[attr-defined]
            yield c


class FakeAmbient(FakeFx):
    def __init__(self):
        super().__init__()
        self.pos = 0.0

    def position(self):
        return self.pos

    async def play(self, path, start=0.0, volume=1.0, fade_in_ms=0, loop=False, owner=None):
        self.current, self.owner = path, owner
        self.calls.append(("play", path, round(start, 2), round(volume, 3), loop, owner))

    async def stop(self, fade_out_ms=0, owner=None):
        # Owner-scoped like the real player: a stop for another owner does nothing.
        self.calls.append(("stop", fade_out_ms, owner))
        if owner is None or owner == getattr(self, "owner", None):
            self.current = self.owner = None

    def ramp(self, volume, ms, then_stop=False):
        self.calls.append(("ramp", round(volume, 3), ms))


class Env:
    """Engine with fake fx/ambient, one fake adopted board and recorded player loads."""

    def __init__(self, c, tmp_path):
        self.c = c
        self.tmp = tmp_path
        self.eng = eng = c.hub.engine
        self.fx = eng.fx = FakeFx()
        self.amb = eng.ambient = FakeAmbient()
        self.sent: list[tuple[str, int | None]] = []
        b = eng.boards
        b.adopted_macs = lambda: ["aa"]
        b.latency_comp_ms = lambda mac: 0.0
        b.set_show_state = lambda mac, state, critical=False, segs=None: self.sent.append(
            (mac, None if state is None else state.bri)
        )
        self.loads: list[tuple[str, float, bool]] = []
        orig = eng.player.load

        async def load(path, start=0.0, loop=False):
            self.loads.append((path, round(start, 2), loop))
            await orig(path, start=start, loop=loop)

        eng.player.load = load
        self.pos = 12.0
        eng.player.position = lambda: self.pos

    def media(self, name, kind="audio", duration=2.0) -> int:
        f = self.tmp / name
        f.write_bytes(b"x")
        with db.session() as s:
            m = Media(filename=name, path=str(f), duration_s=duration, kind=kind)
            s.add(m)
            s.commit()
            s.refresh(m)
            return m.id or 0

    def path(self, name) -> str:
        return str(self.tmp / name)

    async def profile(self, **body) -> int:
        r = await self.c.post("/api/profiles", json=body)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    async def moment(self, variants, **kw) -> int:
        body = {"name": "Doorbell", "icon": "🔔", "color": "#aa0000", **kw, "variants": variants}
        r = await self.c.post("/api/moments", json=body)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def last_bri(self) -> int | None:
        return self.sent[-1][1]


@pytest.fixture
async def env(client, tmp_path):
    return Env(client, tmp_path)


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


def _variant(**kw):
    return {"ambience": "keep", "weight": 1, **kw}


# ---------- model / API ----------


async def test_crud_validation_and_order(env):
    c = env.c
    vid = env.media("v.mp4", "video")
    snd = env.media("organ.ogg")
    vp = await env.profile(name="Dracula", type="video", media_id=vid)
    lp = await env.profile(name="Flicker")
    q = c.hub.bus.subscribe()

    async def bad(variants, **kw):
        body = {"name": "X", "variants": variants, **kw}
        r = await c.post("/api/moments", json=body)
        assert r.status_code == 422, r.text

    await bad([])
    await bad([_variant()])  # no part
    await bad([_variant(video_profile_id=999)])
    await bad([_variant(video_profile_id=lp)])  # light profile as video
    await bad([_variant(light_profile_id=vp)])
    await bad([_variant(sound={"media_id": vid})])  # video media as sound
    await bad([_variant(light_profile_id=lp, ambience="replace")])
    await bad([_variant(light_profile_id=lp, ambience="replace", ambience_media_id=vid)])
    await bad([_variant(light_profile_id=lp)], color="red")

    a = await env.moment(
        [
            _variant(video_profile_id=vp, sound={"media_id": snd}, light_profile_id=lp),
            _variant(light_profile_id=lp, ambience="replace", ambience_media_id=snd, weight=2),
        ],
        busy="interrupt",
        cooldown_s=30,
    )
    b = await env.moment([_variant(sound={"media_id": snd})], name="Scream")
    ms = (await c.get("/api/moments")).json()
    assert [m["id"] for m in ms] == [a, b] and [m["sort"] for m in ms] == [0, 1]
    assert ms[0]["variants"][0]["sound"] == {
        "media_id": snd,
        "volume": 1.0,
        "fade_in_ms": 0,
        "fade_out_ms": 0,
    }
    assert ms[0]["busy"] == "interrupt" and ms[0]["order"] == "shuffle"
    assert ms[1]["variants"][0]["ambience_media_id"] is None

    assert (await c.put("/api/moments/order", json=[b])).status_code == 422
    assert (await c.put("/api/moments/order", json=[b, a])).json() == {"ok": True}
    assert [m["id"] for m in (await c.get("/api/moments")).json()] == [b, a]

    body = {**ms[1], "name": "Scream!", "order": "weighted"}
    r = await c.put(f"/api/moments/{b}", json=body)
    assert r.status_code == 200 and r.json()["name"] == "Scream!" and r.json()["sort"] == 0
    assert (await c.put("/api/moments/999", json=body)).status_code == 404
    assert (await c.delete(f"/api/moments/{b}")).json() == {"ok": True}
    assert (await c.delete(f"/api/moments/{b}")).status_code == 404
    assert [m["id"] for m in (await c.get("/api/moments")).json()] == [a]
    assert (await c.post("/api/moments/999/play")).status_code == 404
    assert (await c.post(f"/api/moments/{a}/play", json={"variant": 5})).status_code == 422

    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert types.count("moments_changed") == 5  # 2 creates, reorder, update, delete
    c.hub.bus.unsubscribe(q)


async def test_show_lock_allows_press_not_edit(env):
    c = env.c
    lp = await env.profile(name="Flicker", idle_length_s=0.5)
    mid = await env.moment([_variant(light_profile_id=lp)])
    body = (await c.get("/api/moments")).json()[0]
    assert (await c.post("/api/show_lock", json={"on": True})).status_code == 200
    assert (await c.post("/api/moments", json=body)).status_code == 423
    assert (await c.put(f"/api/moments/{mid}", json=body)).status_code == 423
    assert (await c.put("/api/moments/order", json=[mid])).status_code == 423
    assert (await c.delete(f"/api/moments/{mid}")).status_code == 423
    assert (await c.get("/api/moments")).status_code == 200
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 200 and r.json()["playing_id"] == mid
    r = await c.post("/api/moments/stop")
    assert r.status_code == 200 and r.json()["playing_id"] is None
    await c.post("/api/show_lock", json={"on": False})


async def test_state_and_ws_snapshot(env):
    c = env.c
    assert (await c.get("/api/moments/state")).json() == {
        "playing_id": None,
        "variant": None,
        "progress": 0.0,
        "elapsed_s": 0.0,
        "duration_s": None,
        "cooldown": {},
    }


def test_ws_snapshot_has_moment(cfg):
    from starlette.testclient import TestClient

    with TestClient(create_app(cfg)) as tc, tc.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
    assert snap["type"] == "snapshot"
    assert snap["data"]["moment"] == {
        "playing_id": None,
        "variant": None,
        "progress": 0.0,
        "elapsed_s": 0.0,
        "duration_s": None,
        "cooldown": {},
    }


def test_migration_up_and_down(tmp_path):
    dbp = tmp_path / "m.db"
    cfg = _cfg(dbp)
    command.upgrade(cfg, "b4e5a6b7c804")
    con = sqlite3.connect(dbp)
    con.execute("INSERT INTO moment (name) VALUES ('Doorbell')")
    con.commit()
    row = con.execute(
        'SELECT icon, sort, cooldown_s, busy, "order", variants FROM moment'
    ).fetchone()
    assert row == ("", 0, 0.0, "ignore", "shuffle", "[]")
    con.close()
    command.downgrade(cfg, "b3d4f5a6b703")
    con = sqlite3.connect(dbp)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "moment" not in tables
    con.close()


# ---------- variant pick ----------


def _spec(n, order="shuffle", weights=None):
    w = weights or [1] * n
    return MomentIn(
        name="M",
        order=order,
        variants=[{"sound": {"media_id": 1}, "weight": w[i]} for i in range(n)],
    )


def test_shuffle_never_repeats_and_covers_all():
    r = MomentRunner(None)  # type: ignore[arg-type]
    r.rng = random.Random(3)
    m = _spec(3)
    picks = [r._pick(1, m) for _ in range(60)]
    assert all(a != b for a, b in zip(picks, picks[1:], strict=False))
    for i in range(0, 60, 3):
        assert sorted(picks[i : i + 3]) == [0, 1, 2]  # bag: each variant once per round
    r2 = MomentRunner(None)  # type: ignore[arg-type]
    r2.rng = random.Random(1)
    w = [r2._pick(2, _spec(3, "weighted", [1, 1, 50])) for _ in range(200)]
    assert all(a != b for a, b in zip(w, w[1:], strict=False))
    assert w.count(2) > 80
    assert {r._pick(3, _spec(1)) for _ in range(5)} == {0}  # a single variant repeats


# ---------- engine ----------


async def _video_home(env, cooldown_s=0.0):
    vid = env.media("show.mp4", "video", 5)
    idle = env.media("fog.mp4", "video", 30)
    wind = env.media("wind.ogg", "audio", 60)
    return await env.profile(
        name="Fog",
        type="video",
        media_id=vid,
        idle_media_id=idle,
        playback_mode="idle_trigger",
        base_state={"bri": 7},
        cooldown_s=cooldown_s,
        ambient={"media_id": wind, "volume": 0.8},
    )


async def test_idle_moment_idle_restores_everything(env):
    c, eng = env.c, env.eng
    home = await _video_home(env, cooldown_s=30)
    drac = env.media("drac.mp4", "video", 4)
    organ = env.media("organ.ogg", "audio", 0.3)
    vp = await env.profile(name="Dracula", type="video", media_id=drac, base_state={"bri": 200})
    lp = await env.profile(name="Flicker", base_state={"bri": 50})
    mid = await env.moment(
        [
            _variant(
                video_profile_id=vp,
                sound={"media_id": organ, "volume": 0.9, "fade_in_ms": 100, "fade_out_ms": 0},
                light_profile_id=lp,
                ambience="mute",
            ),
            _variant(light_profile_id=lp),
        ],
        cooldown_s=20,
    )
    await c.post(f"/api/profiles/{home}/activate")
    assert eng.phase.value == "idle" and env.amb.current == env.path("wind.ogg")
    env.pos = 12.0
    q = c.hub.bus.subscribe()
    eng.moments._history[mid] = [1]  # no repeat: the press must pick variant 0

    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["playing_id"] == mid and st["variant"] == 0
    assert eng.moments.running and eng.home_id() == home
    assert eng.snapshot()["profile"]["id"] == home  # UI keeps seeing the activated profile
    assert eng.playing_main and eng.active.id == vp
    assert env.loads[-1][0] == env.path("drac.mp4")
    assert ("play", env.path("organ.ogg"), 0.0, 0.9, 100, False, "moment") in env.fx.calls
    await _settle()
    assert ("ramp", 0.0, 400) in env.amb.calls
    # Light part on a board the video also drives: the video wins.
    assert env.last_bri() == 200
    assert not (await c.post("/api/show/trigger")).json()["ok"]  # no main show over it
    await eng._finish("ended")  # mpv off: simulate the video's eof
    assert eng.moments.running  # the organ is still playing
    await wait_for(lambda: not eng.moments.running, timeout=3)

    assert eng.active.id == home and eng.phase.value == "idle" and not eng.playing_main
    assert env.loads[-1] == (env.path("fog.mp4"), 12.0, True)  # idle clip resumes
    assert ("ramp", 0.8, 400) == env.amb.calls[-1]  # ambient back, unducked
    assert ("stop", 0, "moment") in env.fx.calls
    assert env.last_bri() == 7  # boards back to the idle base
    assert eng.cooldown_until <= time.monotonic()  # the profile's cooldown is untouched
    st = (await c.get("/api/moments/state")).json()
    assert st["playing_id"] is None and 19 < st["cooldown"][str(mid)] <= 20
    moments = []
    while not q.empty():
        msg = q.get_nowait()
        if msg["type"] == "moment":
            moments.append(msg["data"])
    c.hub.bus.unsubscribe(q)
    assert moments[0]["playing_id"] == mid and moments[-1]["playing_id"] is None
    assert moments[-1]["cooldown"]

    # Cooldown: a plain press does nothing; testing a variant still works.
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 409 and "cooldown" in r.json()["detail"]
    assert "s left" in r.json()["detail"]
    r = await c.post(f"/api/moments/{mid}/play", json={"variant": 1})
    assert r.status_code == 200 and r.json()["variant"] == 1
    assert eng.active.id == lp and env.last_bri() == 50
    assert not eng.playing_main
    await c.post("/api/moments/stop")
    assert eng.active.id == home and env.last_bri() == 7


async def test_second_press_picks_other_variant(env):
    c, eng = env.c, env.eng
    lp1 = await env.profile(name="Red", base_state={"bri": 11})
    lp2 = await env.profile(name="Blue", base_state={"bri": 22})
    mid = await env.moment([_variant(light_profile_id=lp1), _variant(light_profile_id=lp2)])
    seen = []
    for _ in range(6):
        r = await c.post(f"/api/moments/{mid}/play")
        assert r.status_code == 200, r.text
        seen.append(r.json()["variant"])
        await c.post("/api/moments/stop")
    assert all(a != b for a, b in zip(seen, seen[1:], strict=False))
    assert eng.active is None and env.sent[-1] == ("aa", None)  # nothing active: base cleared


async def test_stop_restores_without_reloading_idle(env):
    c, eng = env.c, env.eng
    home = await _video_home(env)
    rain = env.media("rain.ogg", "audio", 60)
    organ = env.media("organ.ogg", "audio", 30)
    lp = await env.profile(name="Flicker", base_state={"bri": 50})
    mid = await env.moment(
        [
            _variant(
                sound={"media_id": organ, "volume": 1, "fade_in_ms": 0, "fade_out_ms": 250},
                light_profile_id=lp,
                ambience="replace",
                ambience_media_id=rain,
            )
        ]
    )
    await c.post(f"/api/profiles/{home}/activate")
    env.amb.pos = 33.0
    loads = len(env.loads)
    assert (await c.post(f"/api/moments/{mid}/play")).status_code == 200
    await _settle()
    assert env.amb.current == env.path("rain.ogg") and env.last_bri() == 50
    r = await c.post("/api/moments/stop")
    assert r.json()["playing_id"] is None
    await _settle()
    assert eng.active.id == home and env.last_bri() == 7 and eng.phase.value == "idle"
    assert len(env.loads) == loads  # the idle clip never stopped: not reloaded
    assert ("stop", 250, "moment") in env.fx.calls
    assert env.amb.calls[-1] == ("play", env.path("wind.ogg"), 33.0, 0.8, True, "ambient")


async def test_busy_policies(env):
    c, eng = env.c, env.eng
    home = await _video_home(env)
    lp = await env.profile(name="Flicker", base_state={"bri": 50})
    mid = await env.moment([_variant(light_profile_id=lp)])
    body = (await c.get("/api/moments")).json()[0]
    await c.post(f"/api/profiles/{home}/activate")

    assert (await c.post("/api/show/trigger")).json()["ok"]
    assert eng.playing_main
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 409 and eng.playing_main  # ignore

    await c.put(f"/api/moments/{mid}", json={**body, "busy": "interrupt"})
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 200 and not eng.playing_main
    assert eng.moments.running and eng.active.id == lp
    # A moment interrupting a moment keeps the first snapshot.
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 200 and eng.home_id() == home
    await c.post("/api/moments/stop")
    assert eng.active.id == home and eng.phase.value == "idle"

    await c.put(f"/api/moments/{mid}", json={**body, "busy": "queue"})
    assert (await c.post("/api/show/trigger")).json()["ok"]
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 200 and r.json()["playing_id"] is None
    assert eng.playing_main and not eng.moments.running
    assert (await c.post(f"/api/moments/{mid}/play")).status_code == 409  # max 1 queued
    await eng._finish("ended")
    await wait_for(lambda: eng.moments.running, timeout=2)
    assert eng.active.id == lp
    await c.post("/api/moments/stop")
    assert eng.active.id == home


async def test_blackout_refuses_and_aborts(env):
    c, eng = env.c, env.eng
    home = await _video_home(env)
    drac = env.media("drac.mp4", "video", 4)
    vp = await env.profile(name="Dracula", type="video", media_id=drac, base_state={"bri": 200})
    mid = await env.moment([_variant(video_profile_id=vp)], busy="queue")
    await c.post(f"/api/profiles/{home}/activate")

    await c.post("/api/blackout", json={"on": True})
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 409 and "blackout" in r.json()["detail"]
    r = await c.post(f"/api/moments/{mid}/play", json={"variant": 0})
    assert r.status_code == 409  # a test press respects blackout too
    await c.post("/api/blackout", json={"on": False})

    assert (await c.post(f"/api/moments/{mid}/play")).status_code == 200
    assert eng.playing_main and eng.active.id == vp
    await c.post("/api/blackout", json={"on": True})
    assert not eng.moments.running and not eng.playing_main
    assert eng.home_id() == home
    await c.post("/api/blackout", json={"on": False})
    assert eng.active.id == home and eng.phase.value == "idle"


async def test_setup_and_video_setup_refuse(env):
    c, eng = env.c, env.eng
    lp = await env.profile(name="Flicker")
    mid = await env.moment([_variant(light_profile_id=lp)])
    eng.test_pattern = True
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 409 and "setup" in r.json()["detail"]
    eng.test_pattern = False
    # A running moment blocks setup actions.
    assert (await c.post(f"/api/moments/{mid}/play")).status_code == 200
    assert eng.setup_blocker() == "a moment is playing"
    await c.post("/api/moments/stop")
    assert eng.setup_blocker() is None


async def test_watchdog_ends_moment_without_video_end(env, monkeypatch):
    from spookhub.show import moments

    monkeypatch.setattr(moments, "WATCHDOG_S", 0.1)
    c, eng = env.c, env.eng
    home = await _video_home(env)
    drac = env.media("drac.mp4", "video", 0.2)
    vp = await env.profile(name="Dracula", type="video", media_id=drac)
    mid = await env.moment([_variant(video_profile_id=vp)])
    await c.post(f"/api/profiles/{home}/activate")
    q = c.hub.bus.subscribe()
    assert (await c.post(f"/api/moments/{mid}/play")).status_code == 200
    assert eng.moments.owns_main()
    # mpv never reports eof: the watchdog ends it after 0.2 + 0.1 s.
    await wait_for(lambda: not eng.moments.running, timeout=2)
    assert eng.active.id == home and eng.phase.value == "idle" and not eng.playing_main
    msgs = []
    while not q.empty():
        m = q.get_nowait()
        if m["type"] == "event":
            msgs.append(m["data"]["message"])
    c.hub.bus.unsubscribe(q)
    assert any("did not report its end" in m for m in msgs)


async def test_profile_delete_strips_moment_parts(env):
    c = env.c
    vid = env.media("v.mp4", "video")
    vp = await env.profile(name="Dracula", type="video", media_id=vid)
    lp = await env.profile(name="Flicker")
    mid = await env.moment(
        [_variant(video_profile_id=vp, light_profile_id=lp), _variant(light_profile_id=lp)]
    )
    q = c.hub.bus.subscribe()
    assert (await c.delete(f"/api/profiles/{lp}")).status_code == 200
    vs = (await c.get("/api/moments")).json()[0]["variants"]
    assert len(vs) == 1 and vs[0]["video_profile_id"] == vp and vs[0]["light_profile_id"] is None
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    c.hub.bus.unsubscribe(q)
    assert "moments_changed" in types
    # Last part gone: the moment stays, with nothing to play.
    assert (await c.delete(f"/api/profiles/{vp}")).status_code == 200
    ms = (await c.get("/api/moments")).json()
    assert [m["id"] for m in ms] == [mid] and ms[0]["variants"] == []
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 409 and "no variants" in r.json()["detail"]


async def test_media_delete_refused_when_moment_uses_it(env):
    c = env.c
    lp = await env.profile(name="Flicker")
    organ = env.media("organ.ogg")
    rain = env.media("rain.ogg")
    await env.moment(
        [
            _variant(sound={"media_id": organ}),
            _variant(light_profile_id=lp, ambience="replace", ambience_media_id=rain),
        ]
    )
    for m in (organ, rain):
        r = await c.delete(f"/api/media/{m}")
        assert r.status_code == 409 and "moment 'Doorbell'" in r.json()["detail"]
    media = {m["id"]: m for m in (await c.get("/api/media")).json()}
    assert media[organ]["used_by"] == ["moment 'Doorbell'"]


async def test_deleting_playing_moment_profile_restores(env):
    c, eng = env.c, env.eng
    home = await _video_home(env)
    lp = await env.profile(name="Flicker", base_state={"bri": 50}, idle_length_s=30)
    mid = await env.moment([_variant(light_profile_id=lp)])
    await c.post(f"/api/profiles/{home}/activate")
    assert (await c.post(f"/api/moments/{mid}/play")).status_code == 200
    assert eng.active.id == lp
    assert (await c.delete(f"/api/profiles/{lp}")).status_code == 200
    assert not eng.moments.running and eng.active.id == home  # home kept, not deactivated


# ---------- review fixes ----------


def _events(c, q, kind="event"):
    out = []
    while not q.empty():
        m = q.get_nowait()
        if m["type"] == kind:
            out.append(m["data"])
    c.hub.bus.unsubscribe(q)
    return out


async def test_light_moment_interrupting_video_moment_blacks_picture(env):
    c, eng = env.c, env.eng
    home = await _video_home(env)
    drac = env.media("drac.mp4", "video", 4)
    vp = await env.profile(name="Dracula", type="video", media_id=drac)
    lp = await env.profile(name="Flicker", base_state={"bri": 50})
    video = await env.moment([_variant(video_profile_id=vp)], busy="interrupt")
    light = await env.moment([_variant(light_profile_id=lp)], name="Flash", busy="interrupt")
    await c.post(f"/api/profiles/{home}/activate")
    assert (await c.post(f"/api/moments/{video}/play")).status_code == 200
    assert eng.moments.owns_main()
    blacks = []

    async def black():
        blacks.append(env.loads[-1][0])

    eng.player.black = black
    assert (await c.post(f"/api/moments/{light}/play")).status_code == 200
    assert blacks == [env.path("drac.mp4")]  # the moment video is taken down
    assert not eng.playing_main and eng.active.id == lp
    await c.post("/api/moments/stop")
    assert env.loads[-1] == (env.path("fog.mp4"), 12.0, True)


async def test_interrupt_show_sets_no_cooldown(env):
    c, eng = env.c, env.eng
    home = await _video_home(env, cooldown_s=60)
    lp = await env.profile(name="Flicker", base_state={"bri": 50})
    mid = await env.moment([_variant(light_profile_id=lp)], busy="interrupt")
    await c.post(f"/api/profiles/{home}/activate")
    assert (await c.post("/api/show/trigger")).json()["ok"]
    assert (await c.post(f"/api/moments/{mid}/play")).status_code == 200
    assert eng.cooldown_until <= time.monotonic()
    await c.post("/api/moments/stop")
    assert (await c.post("/api/show/trigger")).json()["ok"]


async def test_replace_then_keep_without_profile_ambient(env):
    c, eng = env.c, env.eng
    rain = env.media("rain.ogg", "audio", 60)
    home = await env.profile(name="Dark", base_state={"bri": 3})
    lp = await env.profile(name="Flicker", base_state={"bri": 50}, idle_length_s=30)
    rep = await env.moment(
        [_variant(light_profile_id=lp, ambience="replace", ambience_media_id=rain)],
        busy="interrupt",
    )
    keep = await env.moment([_variant(light_profile_id=lp)], name="Keep", busy="interrupt")
    await c.post(f"/api/profiles/{home}/activate")
    assert (await c.post(f"/api/moments/{rep}/play")).status_code == 200
    await _settle()
    # No profile ambient: the replacement plays at full level, not ducked.
    assert ("play", env.path("rain.ogg"), 0.0, 1.0, True, "moment-ambience") in env.amb.calls
    assert (await c.post(f"/api/moments/{keep}/play")).status_code == 200
    await _settle()
    assert env.amb.current is None  # the replacement loop is gone
    # Replace again, then deactivate: the loop stops too.
    assert (await c.post(f"/api/moments/{rep}/play")).status_code == 200
    await _settle()
    assert env.amb.current == env.path("rain.ogg")
    await eng.deactivate()
    await _settle()
    assert env.amb.current is None
    assert not eng.moments._replacing


async def test_replacement_volume_and_abort_resumes_ambient(env, tmp_path):
    c, eng = env.c, env.eng
    home = await _video_home(env)
    rain = env.media("rain.ogg", "audio", 60)
    lp = await env.profile(name="Flicker", base_state={"bri": 50}, idle_length_s=30)
    mid = await env.moment(
        [_variant(light_profile_id=lp, ambience="replace", ambience_media_id=rain)]
    )
    await c.post(f"/api/profiles/{home}/activate")
    assert (await c.post(f"/api/moments/{mid}/play")).status_code == 200
    await _settle()
    assert ("play", env.path("rain.ogg"), 0.0, 0.8, True, "moment-ambience") in env.amb.calls
    env.amb.calls.clear()
    await eng.set_test_pattern(True, str(tmp_path / "grid.png"))
    await wait_for(lambda: env.amb.current == env.path("wind.ogg"), timeout=2)
    assert ("stop", 0, "moment-ambience") in env.amb.calls
    assert env.amb.calls[-1][0] == "play" and env.amb.calls[-1][-1] == "ambient"


async def test_home_edit_during_moment_survives_restore(env):
    c, eng = env.c, env.eng
    home = await _video_home(env)
    lp = await env.profile(name="Flicker", base_state={"bri": 50}, idle_length_s=30)
    mid = await env.moment([_variant(light_profile_id=lp)])
    await c.post(f"/api/profiles/{home}/activate")
    assert (await c.post(f"/api/moments/{mid}/play")).status_code == 200
    body = (await c.get(f"/api/profiles/{home}")).json()
    r = await c.put(f"/api/profiles/{home}", json={**body, "base_state": {"bri": 99}})
    assert r.status_code == 200
    assert eng.active.id == lp and env.last_bri() == 50  # the moment keeps the boards
    await c.post("/api/moments/stop")
    assert eng.active.id == home and eng.active.base.bri == 99 and env.last_bri() == 99


async def test_moment_video_failure_restores_snapshot(env):
    c, eng = env.c, env.eng
    home = await _video_home(env, cooldown_s=0)
    drac = env.media("drac.mp4", "video", 4)
    lp = await env.profile(name="Flicker", base_state={"bri": 50})
    vp = await env.profile(
        name="Dracula", type="video", media_id=drac, idle_media_id=env.media("crypt.mp4", "video")
    )
    mid = await env.moment([_variant(video_profile_id=vp, light_profile_id=lp)], cooldown_s=30)
    await c.post(f"/api/profiles/{home}/activate")
    orig = eng.player.load

    async def load(path, start=0.0, loop=False):
        if path == env.path("drac.mp4"):
            raise RuntimeError("mpv gone")
        await orig(path, start=start, loop=loop)

    eng.player.load = load
    r = await c.post(f"/api/moments/{mid}/play")
    assert r.status_code == 409 and "video could not start" in r.json()["detail"]
    assert not eng.moments.running and eng.active.id == home and not eng.playing_main
    assert env.path("crypt.mp4") not in [x[0] for x in env.loads]  # never the moment's idle
    assert env.loads[-1] == (env.path("fog.mp4"), 12.0, True)
    assert env.last_bri() == 7
    assert str(mid) not in eng.moment_state()["cooldown"]


async def test_moment_ws_only_on_change(env):
    c, eng = env.c, env.eng
    lp = await env.profile(name="Flicker", base_state={"bri": 50}, idle_length_s=0.8)
    mid = await env.moment([_variant(light_profile_id=lp)])
    q = c.hub.bus.subscribe()
    r = await c.post(f"/api/moments/{mid}/play")
    st = r.json()
    assert st["duration_s"] == 0.8 and 0 <= st["elapsed_s"] < 0.5
    await wait_for(lambda: not eng.moments.running, timeout=3)
    msgs = _events(c, q, "moment")
    assert [m["playing_id"] for m in msgs] == [mid, None]  # start + end, nothing in between
    assert msgs[0]["duration_s"] == 0.8 and msgs[1]["duration_s"] is None
