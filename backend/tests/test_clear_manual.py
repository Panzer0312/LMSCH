"""DELETE /api/boards/{mac}/state drops the manual override."""

from spookhub.boards.registry import BoardRuntime, BoardStatus
from spookhub.models import LightState
from tests.test_api import client  # noqa: F401  (fixture)


def _board(hub, mac="aa"):
    b = BoardRuntime(mac=mac, ip="127.0.0.1", name=mac, adopted=True, status=BoardStatus.online)
    hub.boards.boards[mac] = b
    return b


async def test_clear_manual_restores_show_state(client):  # noqa: F811
    boards = client.hub.boards
    b = _board(client.hub)
    b.show_state = LightState(on=True, bri=200)
    boards.set_manual("aa", LightState(bri=50))
    assert boards.desired(b).bri == 50
    boards.clear_manual("aa")
    assert b.manual is None
    assert boards.desired(b).bri == 200


async def test_clear_manual_without_show_state(client):  # noqa: F811
    boards = client.hub.boards
    b = _board(client.hub)
    boards.set_manual("aa", LightState(bri=50))
    boards.clear_manual("aa")
    assert boards.desired(b) is None


async def test_delete_state_route_under_lock(client):  # noqa: F811
    b = _board(client.hub)
    client.hub.boards.set_manual("aa", LightState(bri=50))
    await client.post("/api/show_lock", json={"on": True})
    r = await client.delete("/api/boards/aa/state")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert b.manual is None


async def test_delete_state_unknown_mac(client):  # noqa: F811
    assert (await client.delete("/api/boards/zz/state")).status_code == 404
