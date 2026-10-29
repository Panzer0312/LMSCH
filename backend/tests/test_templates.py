import sqlalchemy as sa

from spookhub import db
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_timeline_api import _setup, _video_profile

CUES = [
    {"offset_s": 0, "duration_s": 1.5, "lane": "all", "state": {"bri": 200}, "label": "Boo"},
    {"offset_s": 2, "lane": "first", "state": {"on": False}, "color": "#112233"},
]


async def test_template_crud(client):  # noqa: F811
    assert (await client.get("/api/templates")).json() == []
    r = await client.post("/api/templates", json={"name": "Jump", "cues": CUES})
    assert r.status_code == 200
    tid = r.json()["id"]
    assert r.json()["cues"][0]["label"] == "Boo" and r.json()["cues"][1]["lane"] == "first"
    assert [t["name"] for t in (await client.get("/api/templates")).json()] == ["Jump"]
    assert (await client.delete(f"/api/templates/{tid}")).status_code == 200
    assert (await client.delete(f"/api/templates/{tid}")).status_code == 404
    assert (await client.get("/api/templates")).json() == []


async def test_template_validation(client):  # noqa: F811
    bad_lane = [{**CUES[0], "lane": "x"}]
    assert (
        await client.post("/api/templates", json={"name": "a", "cues": bad_lane})
    ).status_code == 422
    assert (await client.post("/api/templates", json={"name": "", "cues": CUES})).status_code == 422
    assert (await client.post("/api/templates", json={"name": "a", "cues": []})).status_code == 422


async def test_template_writes_blocked_by_lock(client):  # noqa: F811
    client.hub.settings.update(show_lock=True)
    r = await client.post("/api/templates", json={"name": "Jump", "cues": CUES})
    assert r.status_code == 423
    assert (await client.get("/api/templates")).status_code == 200


async def test_fxdata_fetched_and_exposed(client, farm):  # noqa: F811
    sims, macs = await _setup(client, farm, n=1)
    await client.hub.boards.refresh_meta(macs[0])
    b = next(x for x in (await client.get("/api/state")).json()["boards"] if x["mac"] == macs[0])
    assert len(b["fxdata"]) == len(b["effects"]) > 0
    with db.session() as s:
        raw = s.execute(sa.text("select fxdata from board")).scalar_one()
    assert "Speed" in raw


async def test_fxdata_404_tolerated(client, farm, monkeypatch):  # noqa: F811
    from spookhub.boards.client import WledClient, WledError

    async def boom(self, ip, timeout=3.0):
        raise WledError("404")

    monkeypatch.setattr(WledClient, "fxdata", boom)
    sims, macs = await _setup(client, farm, n=1)
    await client.hub.boards.refresh_meta(macs[0])
    b = client.hub.boards.boards[macs[0]]
    assert b.fxdata == [] and b.effects


async def test_cue_nan_rejected(client):  # noqa: F811
    pid = await _video_profile(client, [])
    r = await client.put(
        f"/api/profiles/{pid}/cues",
        content='[{"start_s": NaN, "state": {"bri": 5}}]',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
