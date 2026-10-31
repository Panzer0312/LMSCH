"""Built-in test presets: idempotent apply, safe remove."""

from sqlmodel import select

from spookhub import db
from spookhub.models import BoardGroup, Cue, Moment, Profile
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_groups import _boards


def _names(model):
    with db.session() as s:
        return sorted(r.name for r in s.exec(select(model)))


async def test_presets_apply_twice_and_remove(client, farm):  # noqa: F811
    macs = await _boards(client, farm, 3)
    mine = (await client.post("/api/profiles", json={"name": "Mine"})).json()["id"]
    listed = (await client.get("/api/presets")).json()
    ids = [p["id"] for p in listed]
    assert {"looks", "lightning", "chase", "rooms", "scene", "rehearsal"} <= set(ids)
    for _ in range(2):  # second run updates in place, no duplicates
        for pid in ids:
            r = await client.post(f"/api/presets/{pid}/apply")
            assert r.status_code == 200, (pid, r.text)
    names = _names(Profile)
    assert len(names) == len(set(names))
    assert "[Test] Rooms yellow/red" in names and "[Test] Shuffle scene" in names
    assert _names(BoardGroup) == ["[Test] Room A", "[Test] Room B"]
    assert _names(Moment) == ["[Test] Lightning strike"]
    with db.session() as s:
        chase = s.exec(select(Profile).where(Profile.name == "[Test] Board chase")).one()
        assert len(s.exec(select(Cue).where(Cue.profile_id == chase.id)).all()) == len(macs)
    st = client.hub.settings.value
    assert st.brightness_cap == 1.0 and st.master_volume == 100  # last applied: Show night
    # The light presets drive real boards.
    await client.post(f"/api/profiles/{chase.id}/activate")
    r = await client.post("/api/moments/1/play")
    assert r.status_code in (200, 409)
    await client.post("/api/presets/remove")
    assert _names(Profile) == ["Mine"] and _names(BoardGroup) == [] and _names(Moment) == []
    assert (await client.get(f"/api/profiles/{mine}")).status_code == 200
    assert (await client.post("/api/presets/nope/apply")).status_code == 404
