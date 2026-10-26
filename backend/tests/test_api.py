import httpx
import pytest
from asgi_lifespan import LifespanManager

from spookhub.main import create_app
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


async def test_board_lifecycle_and_light_profile(client, farm):
    sim = await farm.add(0)
    r = await client.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})
    assert r.status_code == 200
    mac = r.json()["mac"]
    assert not r.json()["adopted"]
    r = await client.post(f"/api/boards/{mac}/adopt", json={"name": "Porch"})
    assert r.json()["adopted"] and r.json()["name"] == "Porch"
    assert len(r.json()["effects"]) > 0

    r = await client.post(
        "/api/profiles",
        json={"name": "Red", "base_state": {"on": True, "bri": 180, "colors": [[255, 0, 0]]}},
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    assert (await client.post(f"/api/profiles/{pid}/activate")).json() == {"ok": True}
    await wait_for(lambda: sim.state["bri"] == 180)

    # Master brightness applies on top of the profile.
    await client.put("/api/settings", json={"master_brightness": 0.5})
    await wait_for(lambda: sim.state["bri"] == 90)

    await client.post("/api/blackout", json={"on": True})
    await wait_for(lambda: sim.state["on"] is False)
    st = (await client.get("/api/state")).json()["show"]
    assert st["blackout"] and st["phase"] == "blackout"
    await client.post("/api/blackout", json={"on": False})
    await wait_for(lambda: sim.state["on"] is True)

    r = await client.patch(f"/api/boards/{mac}", json={"name": "Door"})
    assert r.json()["name"] == "Door"
    dup = (await client.post(f"/api/profiles/{pid}/duplicate")).json()
    assert dup["name"] == "Red (copy)"
    assert (await client.delete(f"/api/profiles/{dup['id']}")).status_code == 200
    assert (await client.delete(f"/api/boards/{mac}")).status_code == 200
    assert (await client.get("/api/boards")).json() == []


async def test_validation_and_trigger_rules(client):
    r = await client.post("/api/profiles", json={"name": "V", "type": "video"})
    assert r.status_code == 422
    r = await client.post("/api/boards/add", json={"ip": "127.0.0.1:1"})
    assert r.status_code == 404
    r = (await client.post("/api/trigger")).json()
    assert r == {"ok": False, "message": "no video profile active"}
    r = await client.post("/api/profiles", json={"name": "L"})
    assert (await client.put(f"/api/profiles/{r.json()['id']}/cues", json=[])).status_code == 422
    assert (await client.delete("/api/media/999")).status_code == 404


async def test_websocket_snapshot(cfg):
    from starlette.testclient import TestClient

    with TestClient(create_app(cfg)) as tc:
        with tc.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "snapshot"
            assert {"boards", "show", "settings", "events"} <= msg["data"].keys()
