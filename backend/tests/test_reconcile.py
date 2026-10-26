import httpx
import pytest

from spookhub import db
from spookhub.boards.client import WledClient, build_payload
from spookhub.boards.registry import BoardManager, BoardStatus
from spookhub.events import EventBus
from spookhub.models import LightState
from tests.conftest import wait_for


@pytest.fixture
async def manager(cfg):
    db.init_engine(cfg.paths.db_path)
    http = httpx.AsyncClient()
    bus = EventBus()
    m = BoardManager(cfg.timing, bus, WledClient(http))
    await m.start()
    yield m
    await m.stop()
    await http.aclose()


async def test_reapply_after_dropout(manager, farm):
    sim = await farm.add(0)
    b = await manager.probe(f"127.0.0.1:{sim.port}", "test")
    assert b is not None
    await manager.adopt(b.mac, "Porch")
    await wait_for(lambda: b.status == BoardStatus.online)

    sim.dropout = True
    await wait_for(lambda: b.status == BoardStatus.offline)
    # Desired state changes while the board is unreachable.
    manager.set_show_state(b.mac, LightState(on=True, bri=42, fx=7), critical=True)
    sim.log.clear()
    sim.dropout = False
    await wait_for(lambda: b.status == BoardStatus.online)
    await wait_for(lambda: any(e["body"].get("bri") == 42 for e in sim.log))
    assert sim.state["bri"] == 42
    assert sim.state["seg"][0]["fx"] == 7
    assert any("state restored" in e["message"] for e in manager.bus.log)


async def test_ip_change_matched_by_mac(manager, farm):
    old = await farm.add(0)
    b = await manager.probe(f"127.0.0.1:{old.port}", "test")
    assert b
    await manager.adopt(b.mac, "Porch")
    manager.set_show_state(b.mac, LightState(on=True, bri=99), critical=False)
    await wait_for(lambda: old.state["bri"] == 99)

    await farm.kill(old)
    await wait_for(lambda: b.status == BoardStatus.offline)
    new = await farm.add(0, mac=old.mac)  # same board, new DHCP address
    found = await manager.probe(f"127.0.0.1:{new.port}", "mDNS")
    assert found is b
    assert b.ip == f"127.0.0.1:{new.port}"
    await wait_for(lambda: new.state["bri"] == 99)
    await wait_for(lambda: b.status == BoardStatus.online)


async def test_unadopted_boards_get_no_commands(manager, farm):
    sim = await farm.add(0)
    b = await manager.probe(f"127.0.0.1:{sim.port}", "test")
    assert b
    manager.set_show_state(b.mac, LightState(on=False), critical=False)
    await wait_for(lambda: b.status == BoardStatus.online)
    assert not any(e["body"] for e in sim.log)


def test_payload_preset_expansion_with_master():
    presets = {"3": {"n": "Spooky", "on": True, "bri": 200, "seg": [{"id": 0, "fx": 2}]}}
    p = build_payload(LightState(preset=3), presets=presets, seg_ids=[0], master=0.5)
    assert p == {"on": True, "bri": 100, "seg": [{"id": 0, "fx": 2}]}
    unknown = build_payload(LightState(preset=9), presets=presets, seg_ids=[0], master=0.5)
    assert unknown == {"ps": 9}


def test_payload_explicit_all_segments():
    st = LightState(on=True, bri=255, fx=1, colors=[(255, 0, 0)], transition_ms=700)
    p = build_payload(st, presets={}, seg_ids=[0, 1], master=1.0)
    assert p["tt"] == 7
    assert [s["id"] for s in p["seg"]] == [0, 1]
    assert p["seg"][1]["col"] == [[255, 0, 0]]
