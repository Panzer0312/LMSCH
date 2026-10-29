"""Phase 3 task 1: display geometry, display profiles, video setup mode."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from pydantic import ValidationError

from spookhub.config import MpvCfg
from spookhub.events import EventBus
from spookhub.models import CropRect, DisplayGeometry, Media
from spookhub.player.geometry import (
    DEFAULT_PROPS,
    DEFAULT_SHADER_OPTS,
    WARP_SHADER,
    crop_px,
    needs_warp,
    to_mpv,
)
from spookhub.player.process import MpvPlayer
from spookhub.show import display
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_migrations import _cfg
from tests.test_setup import FakePlayer

# ---------- geometry (pure) ----------


def test_identity_maps_to_mpv_defaults():
    props, shader = to_mpv(DisplayGeometry(), 1920, 1080)
    assert props == DEFAULT_PROPS
    assert shader == DEFAULT_SHADER_OPTS
    assert set(shader) == set(
        "c0x c0y c1x c1y c2x c2y c3x c3y mask_t mask_r mask_b mask_l feather flip_h flip_v".split()
    )
    assert not needs_warp(DisplayGeometry())


def test_aspect_none_is_minus_one_and_values_pass_through():
    g = DisplayGeometry(
        zoom=0.5, pan_x=-0.2, scale_x=1.5, align_y=1, rotate=90, aspect=1.3333, brightness=-10
    )
    props, _ = to_mpv(g, 0, 0)
    assert props["video-zoom"] == 0.5 and props["video-pan-x"] == -0.2
    assert props["video-scale-x"] == 1.5 and props["video-align-y"] == 1.0
    assert props["video-rotate"] == 90 and props["video-aspect-override"] == 1.3333
    assert props["brightness"] == -10
    assert to_mpv(DisplayGeometry(aspect=None), 0, 0)[0]["video-aspect-override"] == -1.0


def test_shader_opts_corners_mask_flip():
    g = DisplayGeometry(
        corners=[(0.1, 0.0), (0.0, -0.05), (0, 0), (-0.25, 0.5)],
        mask={"top": 0.1, "right": 0, "bottom": 0, "left": 0.02, "feather": 0.05},
        flip_h=True,
    )
    _, sh = to_mpv(g, 1920, 1080)
    assert sh["c0x"] == "0.1" and sh["c1y"] == "-0.05" and sh["c3x"] == "-0.25"
    assert sh["c3y"] == "0.5" and sh["mask_t"] == "0.1" and sh["mask_l"] == "0.02"
    assert sh["feather"] == "0.05" and sh["flip_h"] == "1" and sh["flip_v"] == "0"
    assert needs_warp(g)
    assert needs_warp(DisplayGeometry(flip_v=True))
    # Feather alone draws nothing.
    assert not needs_warp(DisplayGeometry(mask={"feather": 0.2}))


@pytest.mark.parametrize(
    ("rotate", "expected"),
    [
        # Displayed crop = left half, top 3/4. Source is 1000x500.
        (0, "500x375+0+0"),
        # 90 cw: displayed left half = source bottom half; displayed top = source left.
        (90, "750x250+0+250"),
        (180, "500x375+500+125"),
        # 270 cw: displayed left half = source top half; displayed top = source right.
        (270, "750x250+250+0"),
    ],
)
def test_crop_px_with_rotation(rotate, expected):
    c = CropRect(x=0, y=0, w=0.5, h=0.75)
    assert crop_px(c, rotate, 1000, 500) == expected


def test_crop_full_or_unknown_size_is_empty():
    assert crop_px(CropRect(), 0, 1920, 1080) == ""
    assert crop_px(CropRect(x=0.1, w=0.5), 0, 0, 0) == ""
    assert crop_px(CropRect(x=0.25, y=0.1, w=0.5, h=0.8), 0, 1920, 1080) == "960x864+480+108"


def test_geometry_validation():
    with pytest.raises(ValidationError):
        DisplayGeometry(rotate=45)
    with pytest.raises(ValidationError):
        DisplayGeometry(corners=[(0, 0)] * 3)
    with pytest.raises(ValidationError):
        DisplayGeometry(corners=[(0.6, 0)] + [(0, 0)] * 3)
    with pytest.raises(ValidationError):
        CropRect(x=0.6, w=0.5)
    with pytest.raises(ValidationError):
        DisplayGeometry(mask={"top": 0.6})
    with pytest.raises(ValidationError):
        DisplayGeometry(zoom=float("nan"))


def test_warp_shader_is_packaged_and_declares_params():
    src = WARP_SHADER.read_text()
    assert WARP_SHADER.parent.name == "shaders"
    for k in DEFAULT_SHADER_OPTS:
        assert f"//!PARAM {k}\n" in src
    assert "//!HOOK OUTPUT" in src


# ---------- player: diff-only, coalesced, re-applied ----------


class RecIpc:
    connected = True

    def __init__(self):
        self.sets: list[tuple[str, object]] = []
        self.cmds: list[tuple] = []

    async def set_property(self, name, value):
        self.sets.append((name, value))

    async def command(self, *args, timeout=2.0):
        self.cmds.append(args)


def _ready_player() -> tuple[MpvPlayer, RecIpc]:
    p = MpvPlayer(MpvCfg(enabled=True), EventBus())
    ipc = RecIpc()
    p._ipc = ipc  # type: ignore[assignment]
    p.ready.set()
    return p, ipc


def test_args_always_load_warp_shader():
    p = MpvPlayer(MpvCfg(enabled=True), EventBus())
    assert f"--glsl-shaders={WARP_SHADER}" in p.args()


async def test_apply_geometry_coalesces_and_sends_only_changes():
    p, ipc = _ready_player()
    for z in (0.1, 0.2, 0.3):
        p.apply_geometry(DisplayGeometry(zoom=z))
    await asyncio.sleep(0.1)
    assert ipc.sets == [("video-zoom", 0.3)]
    ipc.sets.clear()
    p.apply_geometry(DisplayGeometry(zoom=0.3, flip_h=True))
    await p.flush_geometry()
    assert [n for n, _ in ipc.sets] == ["glsl-shader-opts"]
    assert ipc.sets[0][1]["flip_h"] == "1"  # type: ignore[index]
    ipc.sets.clear()
    p.apply_geometry(DisplayGeometry(zoom=0.3, flip_h=True))
    await p.flush_geometry()
    assert ipc.sets == []


async def test_load_runs_hook_and_applies_before_loadfile():
    p, ipc = _ready_player()
    seen = []

    def hook(path):
        seen.append(path)
        return DisplayGeometry(crop={"x": 0, "y": 0, "w": 0.5, "h": 1}), (640, 480)

    p.geometry_for = hook
    await p.load("/v.mp4")
    assert seen == ["/v.mp4"]
    crop_i = ipc.sets.index(("video-crop", "320x480+0+0"))
    # The geometry went out before the file was loaded.
    assert ipc.cmds and ipc.cmds[0][0] == "loadfile"
    assert crop_i >= 0
    # A second load of the same thing sends no geometry again.
    ipc.sets.clear()
    await p.load("/v.mp4")
    assert not any(n == "video-crop" for n, _ in ipc.sets)


async def test_disabled_player_still_runs_hook():
    p = MpvPlayer(MpvCfg(enabled=False), EventBus())
    p.geometry_for = lambda path: (DisplayGeometry(zoom=1), None)
    await p.load("/x.mp4")
    assert p.geometry.zoom == 1


# ---------- resolution ----------


def _media(path="/m.mp4", w=1920, h=1080, dp=None) -> int:
    from spookhub import db

    with db.session() as s:
        m = Media(filename=Path(path).name, path=path, width=w, height=h, display_profile_id=dp)
        s.add(m)
        s.commit()
        s.refresh(m)
        return m.id or 0


async def _dp(client, name, **geom):  # noqa: F811
    r = await client.post("/api/display/profiles", json={"name": name, "geometry": geom})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_precedence_profile_media_global(client):  # noqa: F811
    g_id = await _dp(client, "global", zoom=0.1)
    m_id = await _dp(client, "media", zoom=0.2)
    p_id = await _dp(client, "profile", zoom=0.3)
    assert display.resolve(None, None, None) == (None, DisplayGeometry())
    assert display.resolve(None, None, g_id)[0] == g_id
    assert display.resolve(None, m_id, g_id)[0] == m_id
    assert display.resolve(p_id, m_id, g_id)[0] == p_id
    assert display.resolve(999, None, g_id)[0] == g_id  # dangling id falls through

    eng = client.hub.engine
    mid = _media(dp=m_id)
    await client.put("/api/settings", json={"display_profile_id": g_id})
    g, size = eng._geometry_for("/m.mp4")
    assert g.zoom == 0.2 and size == (1920, 1080, 0)
    assert eng.display_state()["resolved_profile_id"] == m_id
    g, size = eng._geometry_for("/other.png")
    assert g.zoom == 0.1 and size is None
    r = await client.post(
        "/api/profiles",
        json={"name": "V", "type": "video", "media_id": mid, "display_profile_id": p_id},
    )
    assert r.json()["display_profile_id"] == p_id
    await eng.activate(r.json()["id"], autostart=False)
    assert eng._geometry_for("/m.mp4")[0].zoom == 0.3
    bad = await client.put(
        f"/api/profiles/{r.json()['id']}",
        json={"name": "V", "type": "video", "media_id": mid, "display_profile_id": 999},
    )
    assert bad.status_code == 422


# ---------- routes ----------


async def test_profile_crud_and_delete_clears_references(client):  # noqa: F811
    seen = []
    orig = client.hub.bus.publish
    client.hub.bus.publish = lambda t, d=None: (seen.append(t), orig(t, d))  # type: ignore[method-assign]
    a = await _dp(client, "Window", corners=[[0.1, 0], [0, 0], [0, 0], [0, 0]])
    assert (
        await client.post("/api/display/profiles", json={"name": "Window", "geometry": {}})
    ).status_code == 409
    lst = (await client.get("/api/display/profiles")).json()
    assert lst[0]["name"] == "Window" and lst[0]["geometry"]["corners"][0] == [0.1, 0]
    r = await client.put(f"/api/display/profiles/{a}", json={"name": "Win"})
    assert r.json()["name"] == "Win" and r.json()["geometry"]["corners"][0] == [0.1, 0]
    r = await client.put(f"/api/display/profiles/{a}", json={"geometry": {"zoom": 1}})
    assert r.json()["name"] == "Win" and r.json()["geometry"]["zoom"] == 1
    b = await _dp(client, "Other")
    assert (await client.put(f"/api/display/profiles/{b}", json={"name": "Win"})).status_code == 409
    assert (await client.put("/api/display/profiles/999", json={"name": "x"})).status_code == 404

    mid = _media()
    r = await client.patch(f"/api/media/{mid}", json={"display_profile_id": a})
    assert r.json() == {"id": mid, "display_profile_id": a}
    assert (
        await client.patch(f"/api/media/{mid}", json={"display_profile_id": 999})
    ).status_code == 422
    assert (await client.patch("/api/media/999", json={"display_profile_id": a})).status_code == 404
    pid = (
        await client.post(
            "/api/profiles", json={"name": "L", "type": "light", "display_profile_id": a}
        )
    ).json()["id"]
    await client.put("/api/settings", json={"display_profile_id": a})

    assert (await client.delete(f"/api/display/profiles/{a}")).status_code == 200
    assert client.hub.settings.value.display_profile_id is None
    assert (await client.get(f"/api/profiles/{pid}")).json()["display_profile_id"] is None
    assert display.media_by_id(mid)[1].display_profile_id is None  # type: ignore[index]
    assert "display_profiles_changed" in seen and "media_changed" in seen


async def _stub_patterns(monkeypatch, tmp_path):
    from spookhub.show import patterns

    async def ensure(data_dir, id, size=(1920, 1080)):  # noqa: A002
        if id not in ("grid", "corners"):
            raise KeyError(id)
        return tmp_path / f"{id}.png"

    monkeypatch.setattr(patterns, "ensure", ensure)


async def test_setup_mode_transitions(client, monkeypatch, tmp_path):  # noqa: F811
    await _stub_patterns(monkeypatch, tmp_path)
    eng = client.hub.engine
    fake = FakePlayer()
    eng.player = fake
    states = []
    orig = client.hub.bus.publish
    client.hub.bus.publish = lambda t, d=None: (  # type: ignore[method-assign]
        states.append(d) if t == "display" else None,
        orig(t, d),
    )

    st = (await client.get("/api/display/state")).json()
    assert st["setup"] is False and st["source"] is None and st["live"] is None
    assert st["geometry"] == DisplayGeometry().model_dump(mode="json") and "warp_supported" in st

    # live and seek are refused outside setup
    assert (
        await client.post("/api/display/live", json={"geometry": {"zoom": 1}})
    ).status_code == 409
    assert (await client.post("/api/display/seek", json={"t": 1})).status_code == 409

    r = await client.post("/api/display/setup", json={"on": True})
    assert r.status_code == 200
    assert r.json()["setup"] and r.json()["source"] == {"kind": "pattern", "id": "grid"}
    assert fake.calls[-1] == ("load", str(tmp_path / "grid.png"))
    snap = eng.snapshot()
    assert snap["test_pattern"] and snap["phase"] == "test_pattern"
    assert (await client.post("/api/show/trigger")).json()["ok"] is False

    assert (
        await client.post(
            "/api/display/setup", json={"on": True, "source": {"kind": "pattern", "id": "nope"}}
        )
    ).status_code == 404
    mid = _media("/clip.mp4", 640, 480)
    r = await client.post(
        "/api/display/setup", json={"on": True, "source": {"kind": "media", "id": mid}}
    )
    assert r.json()["source"] == {"kind": "media", "id": mid}
    assert fake.calls[-1] == ("load", "/clip.mp4") and eng._setup_size == (640, 480, 0)
    assert (await client.post("/api/display/seek", json={"t": 2.5})).status_code == 200
    assert (
        await client.post(
            "/api/display/setup", json={"on": True, "source": {"kind": "media", "id": 999}}
        )
    ).status_code == 404

    r = await client.post("/api/display/live", json={"geometry": {"zoom": 1, "flip_h": True}})
    assert r.status_code == 200
    st = states[-1]
    assert st["setup"] and st["live"]["zoom"] == 1 and st["geometry"]["flip_h"] is True
    assert fake.geometry.zoom == 1 and fake._geom_size == (640, 480, 0)

    # idle source without an idle video shows black
    r = await client.post("/api/display/setup", json={"on": True, "source": {"kind": "idle"}})
    assert r.json()["source"] == {"kind": "idle"} and fake.calls[-1] == ("black",)
    assert (await client.post("/api/display/seek", json={"t": 1})).status_code == 409

    r = await client.post("/api/display/setup", json={"on": False})
    assert r.json()["setup"] is False and r.json()["live"] is None
    assert not eng.test_pattern and eng.phase.value == "none"
    # Unsaved live geometry is dropped on exit.
    assert fake.geometry == DisplayGeometry()
    assert states[-1]["setup"] is False


async def test_setup_refused_on_blackout_and_cleared_by_it(client, monkeypatch, tmp_path):  # noqa: F811
    await _stub_patterns(monkeypatch, tmp_path)
    eng = client.hub.engine
    eng.player = FakePlayer()
    await client.post("/api/display/setup", json={"on": True})
    await client.post("/api/display/live", json={"geometry": {"zoom": 1}})
    await client.post("/api/blackout", json={"on": True})
    assert not eng.display_setup and eng.live is None and eng.phase.value == "blackout"
    assert (await client.post("/api/display/setup", json={"on": True})).status_code == 409
    await client.post("/api/blackout", json={"on": False})
    assert (await client.post("/api/display/setup", json={"on": True})).status_code == 200
    # The old test pattern endpoint takes over from setup and leaves setup mode.
    assert (await client.post("/api/show/test_pattern", json={"on": False})).status_code == 200
    assert not eng.display_setup


async def test_setup_stops_running_show_and_restores_idle(client, monkeypatch, tmp_path):  # noqa: F811
    await _stub_patterns(monkeypatch, tmp_path)
    from tests.test_resilience import StallPlayer

    eng = client.hub.engine
    p = StallPlayer()
    eng.player = p
    p.on_change = eng._on_player_change
    idle = tmp_path / "idle.mp4"
    main = tmp_path / "main.mp4"
    idle.write_bytes(b"x")
    main.write_bytes(b"x")
    m1, m2 = _media(str(main)), _media(str(idle))
    pid = (
        await client.post(
            "/api/profiles",
            json={
                "name": "V",
                "type": "video",
                "media_id": m1,
                "idle_media_id": m2,
                "playback_mode": "idle_trigger",
            },
        )
    ).json()["id"]
    await eng.activate(pid)
    assert (await client.post("/api/show/trigger")).json()["ok"]
    assert eng.playing_main
    await client.post("/api/display/setup", json={"on": True})
    assert not eng.playing_main and eng.display_setup
    await client.post("/api/display/setup", json={"on": False})
    assert eng.phase.value == "idle" and p.loads[-1][0] == str(idle)


async def test_lock_allows_only_setup_off(client, monkeypatch, tmp_path):  # noqa: F811
    await _stub_patterns(monkeypatch, tmp_path)
    eng = client.hub.engine
    eng.player = FakePlayer()
    await client.post("/api/display/setup", json={"on": True})
    client.hub.settings.update(show_lock=True)
    assert (await client.post("/api/display/setup", json={"on": True})).status_code == 423
    assert (await client.post("/api/display/live", json={"geometry": {}})).status_code == 423
    assert (
        await client.post("/api/display/profiles", json={"name": "x", "geometry": {}})
    ).status_code == 423
    assert (await client.post("/api/display/setup", json={"on": False})).status_code == 200
    assert not eng.display_setup


async def test_lock_on_ends_setup(client, monkeypatch, tmp_path):  # noqa: F811
    await _stub_patterns(monkeypatch, tmp_path)
    eng = client.hub.engine
    eng.player = FakePlayer()
    await client.post("/api/display/setup", json={"on": True})
    assert (await client.post("/api/show_lock", json={"on": True})).status_code == 200
    assert not eng.display_setup and not eng.test_pattern


async def test_export_import_carries_display_profile_by_name(client):  # noqa: F811
    a = await _dp(client, "Scrim", zoom=0.4)
    pid = (
        await client.post(
            "/api/profiles", json={"name": "L", "type": "light", "display_profile_id": a}
        )
    ).json()["id"]
    data = (await client.get(f"/api/profiles/{pid}/export")).json()
    assert data["display_profile"]["name"] == "Scrim"
    assert data["display_profile"]["geometry"]["zoom"] == 0.4
    assert "display_profile_id" not in data["profile"]

    # Same name exists: reused, never overwritten.
    data["display_profile"]["geometry"]["zoom"] = 1.5
    r = await client.post("/api/profiles/import", json={"data": data})
    assert r.status_code == 201 and r.json()["display_profile_id"] == a
    assert (await client.get("/api/display/profiles")).json()[0]["geometry"]["zoom"] == 0.4

    # Unknown name: created.
    data["display_profile"]["name"] = "New hub"
    r = await client.post("/api/profiles/import", json={"data": data})
    new_id = r.json()["display_profile_id"]
    assert new_id not in (None, a)
    names = {p["name"]: p for p in (await client.get("/api/display/profiles")).json()}
    assert names["New hub"]["geometry"]["zoom"] == 1.5

    data["display_profile"] = None
    r = await client.post("/api/profiles/import", json={"data": data})
    assert r.json()["display_profile_id"] is None

    data["display_profile"] = {"name": "Bad", "geometry": {"rotate": 45}}
    assert (await client.post("/api/profiles/import", json={"data": data})).status_code == 422


async def test_preflight_warns_when_warp_needs_gpu_next(client):  # noqa: F811
    eng = client.hub.engine
    eng.player = FakePlayer()  # enabled cfg, default vo=gpu
    eng._shown = DisplayGeometry(corners=[(0.1, 0), (0, 0), (0, 0), (0, 0)])
    res = await client.hub.preflight.run()
    chk = next(c for c in res["checks"] if c["id"] == "warp")
    assert chk["status"] == "warn" and "gpu-next" in chk["detail"]
    assert eng.display_state()["warp_supported"] is False
    client.hub.settings.update(mpv_vo="gpu-next")
    res = await client.hub.preflight.run()
    assert next(c for c in res["checks"] if c["id"] == "warp")["status"] == "ok"
    assert eng.display_state()["warp_supported"] is True


async def test_ws_snapshot_has_display(cfg):
    from starlette.testclient import TestClient

    from spookhub.main import create_app

    with TestClient(create_app(cfg)) as tc, tc.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["data"]["display"]["setup"] is False


def test_display_migration_up_and_down(tmp_path):
    db = tmp_path / "d.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "e8a4c2f61b37")
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO media (filename, path, size, duration_s, width, height, codec, created_at) "
        "VALUES ('a','/a',0,0,0,0,'','2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()
    command.upgrade(cfg, "f3b8d1c5a274")
    con = sqlite3.connect(db)
    assert con.execute("SELECT display_profile_id FROM media").fetchone()[0] is None
    con.execute("INSERT INTO displayprofile (name, geometry) VALUES ('x', '{}')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO displayprofile (name, geometry) VALUES ('x', '{}')")
    con.close()
    command.downgrade(cfg, "e8a4c2f61b37")
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(profile)")}
    assert "display_profile_id" not in cols
    con.close()
