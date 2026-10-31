"""Phase 6: board groups (rooms)."""

from spookhub.boards.groups import resolve_overrides, resolve_selection, resolve_targets
from spookhub.models import BoardSelection, CueTarget, LightState
from spookhub.show.engine import load_active
from spookhub.show.resolve import CueSpec, Timeline
from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_timeline_api import _video_profile

GROUPS = {1: ["a", "b", "c"], 2: ["c", "d"], 3: []}


def test_resolve_selection_and_targets():
    sel = resolve_selection(BoardSelection(all=False, macs=["x"], groups=[1, 99]), GROUPS)
    assert sel.macs == ["x", "a", "b", "c"] and sel.groups == []
    assert resolve_selection(BoardSelection(all=True, groups=[1]), GROUPS).all
    assert resolve_targets(CueTarget(all=True, groups=[1]), GROUPS) is None
    assert resolve_targets(CueTarget(all=False, groups=[2]), GROUPS) == {"c", "d"}
    # Unknown or empty group: the cue hits nothing instead of everything.
    assert resolve_targets(CueTarget(all=False, groups=[3, 99]), GROUPS) == frozenset()


def test_resolve_overrides_order():
    raw = {
        "group:1": {"colors": [[255, 255, 0]], "bri": 10},
        "group:2": {"colors": [[255, 0, 0]]},
        "c": {"bri": 99},
    }
    out = resolve_overrides(raw, GROUPS)
    assert out["a"] == LightState(colors=[(255, 255, 0)], bri=10)
    assert out["d"] == LightState(colors=[(255, 0, 0)])
    # c is in both groups: the later group's colour, then the board key on top.
    assert out["c"] == LightState(colors=[(255, 0, 0)], bri=99)


def test_group_cue_overlap_latest_start_wins():
    yellow, red = LightState(fx=1), LightState(fx=2)
    cues = [
        CueSpec(1, 0.0, None, frozenset({"a", "b"}), yellow),
        CueSpec(2, 1.0, None, frozenset({"b"}), red),
    ]
    tl = Timeline(cues, ["a", "b"])
    assert tl.active("a", 2).id == 1
    assert tl.active("b", 2).id == 2


async def _boards(client, farm, n):  # noqa: F811
    macs = []
    for i in range(n):
        sim = await farm.add(i)
        r = await client.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})
        mac = r.json()["mac"]
        await client.post(f"/api/boards/{mac}/adopt", json={"name": f"B{i}"})
        macs.append(mac)
    return macs


async def test_group_crud_order_and_delete_guard(client, farm):  # noqa: F811
    m = await _boards(client, farm, 2)
    r = await client.post("/api/groups", json={"name": "Dining", "macs": [m[0], m[0]]})
    assert r.status_code == 201
    g1 = r.json()
    assert g1["macs"] == [m[0]] and g1["sort"] == 0
    g2 = (await client.post("/api/groups", json={"name": "Hall", "color": "#ff0000"})).json()
    assert (await client.post("/api/groups", json={"name": "X", "macs": ["zz"]})).status_code == 422
    r = await client.put(f"/api/groups/{g2['id']}", json={"name": "Hall", "macs": m})
    assert sorted(r.json()["macs"]) == sorted(m)
    assert (await client.put("/api/groups/order", json=[g2["id"], g1["id"]])).status_code == 200
    names = [g["name"] for g in (await client.get("/api/groups")).json()]
    assert names == ["Hall", "Dining"]
    # In use by a profile: refused with the user's name.
    await client.post(
        "/api/profiles",
        json={"name": "Room", "selection": {"all": False, "groups": [g1["id"]]}},
    )
    r = await client.delete(f"/api/groups/{g1['id']}")
    assert r.status_code == 409 and "Room" in r.json()["detail"]
    assert (await client.delete(f"/api/groups/{g2['id']}")).status_code == 200
    # Removing a board drops it from its groups.
    await client.delete(f"/api/boards/{m[0]}")
    assert (await client.get("/api/groups")).json()[0]["macs"] == []


async def test_group_cue_drives_members_and_follows_membership(client, farm):  # noqa: F811
    m = await _boards(client, farm, 4)
    room = (await client.post("/api/groups", json={"name": "Dining", "macs": m[:3]})).json()
    cues = [{"start_s": 0, "target": {"all": False, "groups": [room["id"]]}, "state": {"fx": 7}}]
    pid = await _video_profile(client, cues)
    prof = load_active(pid)
    assert prof is not None and prof.cues[0].targets == frozenset(m[:3])
    await client.post(f"/api/profiles/{pid}/activate")
    eng = client.hub.engine
    assert eng.timeline is not None
    assert {mac for mac in m if eng.timeline.active(mac, 1)} == set(m[:3])
    # Moving the fourth board into the room changes the live result without editing the cue.
    await client.put(f"/api/groups/{room['id']}", json={"name": "Dining", "macs": m})
    await wait_for(lambda: eng.timeline is not None and eng.timeline.active(m[3], 1) is not None)


async def test_group_overrides_on_boards(client, farm):  # noqa: F811
    m = await _boards(client, farm, 4)
    dining = (await client.post("/api/groups", json={"name": "Dining", "macs": m[:2]})).json()
    hall = (await client.post("/api/groups", json={"name": "Hall", "macs": m[2:]})).json()
    body = {
        "name": "Rooms",
        "base_state": {"on": True, "bri": 100},
        "overrides": {
            f"group:{dining['id']}": {"colors": [[255, 255, 0]]},
            f"group:{hall['id']}": {"colors": [[255, 0, 0]]},
        },
    }
    pid = (await client.post("/api/profiles", json=body)).json()["id"]
    await client.post(f"/api/profiles/{pid}/activate")
    bm = client.hub.boards
    await wait_for(lambda: all(bm.boards[x].show_state is not None for x in m))
    assert bm.boards[m[0]].show_state.colors == [(255, 255, 0)]
    assert bm.boards[m[3]].show_state.colors == [(255, 0, 0)]


async def test_group_actions_and_preflight(client, farm):  # noqa: F811
    m = await _boards(client, farm, 2)
    g = (await client.post("/api/groups", json={"name": "Hall", "macs": [m[1]]})).json()
    r = await client.post(f"/api/groups/{g['id']}/state", json={"on": False})
    assert r.json()["macs"] == [m[1]]
    bm = client.hub.boards
    assert bm.boards[m[1]].manual == LightState(on=False) and bm.boards[m[0]].manual is None
    await client.delete(f"/api/groups/{g['id']}/state")
    assert bm.boards[m[1]].manual is None
    assert (await client.post(f"/api/groups/{g['id']}/identify")).status_code == 200
    empty = (await client.post("/api/groups", json={"name": "Empty"})).json()
    body = {"name": "P", "selection": {"all": False, "groups": [empty["id"]]}}
    pid = (await client.post("/api/profiles", json=body)).json()["id"]
    await client.post(f"/api/profiles/{pid}/activate")
    res = await client.hub.preflight.run()
    chk = next(c for c in res["checks"] if c["id"] == "groups")
    assert chk["status"] == "warn" and "no boards" in chk["detail"]


async def test_export_import_maps_groups_by_name(client, farm):  # noqa: F811
    m = await _boards(client, farm, 1)
    g = (await client.post("/api/groups", json={"name": "Hall", "macs": m})).json()
    cues = [
        {"start_s": 0, "target": {"all": False, "groups": [g["id"]]}, "state": {"fx": 1}},
    ]
    pid = await _video_profile(client, cues)
    p = (await client.get(f"/api/profiles/{pid}")).json()
    p["overrides"] = {f"group:{g['id']}": {"bri": 5}}
    p["selection"] = {"all": False, "macs": [], "groups": [g["id"]]}
    await client.put(f"/api/profiles/{pid}", json=p)
    data = (await client.get(f"/api/profiles/{pid}/export")).json()
    assert data["groups"] == {str(g["id"]): "Hall"}
    # A cue on a room the target hub doesn't know is dropped on import.
    data["cues"].append(
        {"start_s": 1, "target": {"all": False, "groups": [12345]}, "state": {"fx": 2}}
    )
    # Same name on the target hub, different id.
    await client.put(f"/api/groups/{g['id']}", json={"name": "Old", "macs": m})
    new = (await client.post("/api/groups", json={"name": "Hall", "macs": m})).json()
    from sqlmodel import select

    from spookhub import db
    from spookhub.models import Media

    with db.session() as s:
        mid = s.exec(select(Media)).first().id
    r = await client.post("/api/profiles/import", json={"data": data, "media_id": mid})
    imp = r.json()
    assert imp["selection"]["groups"] == [new["id"]]
    assert list(imp["overrides"]) == [f"group:{new['id']}"]
    assert [c["target"]["groups"] for c in imp["cues"]] == [[new["id"]]]  # unknown dropped


async def test_group_validation_members_and_board_remove(client, farm):  # noqa: F811
    m = await _boards(client, farm, 2)
    g = (await client.post("/api/groups", json={"name": "Hall"})).json()
    assert (await client.post("/api/groups", json={"name": " Hall "})).status_code == 409
    assert (await client.post("/api/groups", json={"name": "   "})).status_code == 422
    r = await client.post(
        "/api/profiles", json={"name": "P", "selection": {"all": False, "groups": [99]}}
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/profiles", json={"name": "P", "overrides": {"group:99": {"bri": 1}}}
    )
    assert r.status_code == 422
    # Single-member join/leave never overwrites another board's membership.
    await client.put(f"/api/groups/{g['id']}/members/{m[0]}")
    r = await client.put(f"/api/groups/{g['id']}/members/{m[1]}")
    assert sorted(r.json()["macs"]) == sorted(m)
    assert (await client.put(f"/api/groups/{g['id']}/members/zz")).status_code == 422
    r = await client.delete(f"/api/groups/{g['id']}/members/{m[1]}")
    assert r.json()["macs"] == [m[0]]
    cues = [{"start_s": 0, "target": {"all": False, "groups": [g["id"]]}, "state": {"fx": 3}}]
    pid = await _video_profile(client, cues)
    bad = [{"start_s": 0, "target": {"all": False, "groups": [99]}, "state": {"fx": 3}}]
    assert (await client.put(f"/api/profiles/{pid}/cues", json=bad)).status_code == 422
    await client.post(f"/api/profiles/{pid}/activate")
    eng = client.hub.engine
    assert eng.active is not None and eng.active.cues[0].targets == frozenset({m[0]})
    await client.delete(f"/api/boards/{m[0]}")
    assert eng.active.cues[0].targets == frozenset()
