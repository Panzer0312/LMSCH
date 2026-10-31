from tests.test_api import client  # noqa: F401  (fixture)


async def test_board_identify_route(client, farm):  # noqa: F811
    sim = await farm.add(0)
    r = await client.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})
    mac = r.json()["mac"]
    await client.post(f"/api/boards/{mac}/adopt", json={"name": "A"})
    assert (await client.post(f"/api/boards/{mac}/identify")).status_code == 200
