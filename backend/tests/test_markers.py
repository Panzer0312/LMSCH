"""Cue label/color and profile markers."""

from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_timeline_api import _video_profile


async def test_cue_label_color_roundtrip(client):  # noqa: F811
    pid = await _video_profile(
        client, [{"start_s": 1, "state": {"bri": 5}, "label": "Boo", "color": "#ff8800"}]
    )
    cue = (await client.get(f"/api/profiles/{pid}")).json()["cues"][0]
    assert cue["label"] == "Boo" and cue["color"] == "#ff8800"


async def test_cue_color_validation(client):  # noqa: F811
    pid = await _video_profile(client, [])
    for bad in ("red", "#fff", "#gggggg"):
        r = await client.put(
            f"/api/profiles/{pid}/cues", json=[{"start_s": 0, "state": {"bri": 1}, "color": bad}]
        )
        assert r.status_code == 422


async def _markers(client, pid):  # noqa: F811
    return (await client.get(f"/api/profiles/{pid}")).json()["markers"]


async def test_markers_put_sorted_with_defaults(client):  # noqa: F811
    pid = await _video_profile(client, [])
    ms = [{"id": "b", "t": 5, "label": "late", "color": "#00ff00"}, {"id": "a", "t": 1.5}]
    r = await client.put(f"/api/profiles/{pid}/markers", json=ms)
    assert r.status_code == 200
    got = r.json()["markers"]
    assert [m["id"] for m in got] == ["a", "b"]
    assert got[0]["label"] == "" and got[0]["color"] is None


async def test_markers_put_invalid(client):  # noqa: F811
    pid = await _video_profile(client, [])
    for bad in (
        [{"id": "x", "t": -1}],
        [{"id": "x", "t": 1, "color": "nope"}],
        [{"id": "x", "t": 1}, {"id": "x", "t": 2}],
        [{"id": "x", "t": 1, "label": "l" * 201}],
    ):
        assert (await client.put(f"/api/profiles/{pid}/markers", json=bad)).status_code == 422


async def test_markers_t_zero_clear_and_404(client):  # noqa: F811
    pid = await _video_profile(client, [])
    r = await client.put(f"/api/profiles/{pid}/markers", json=[{"id": "z", "t": 0}])
    assert r.status_code == 200 and len(r.json()["markers"]) == 1
    r = await client.put(f"/api/profiles/{pid}/markers", json=[])
    assert r.status_code == 200 and await _markers(client, pid) == []
    assert (await client.put("/api/profiles/9999/markers", json=[])).status_code == 404


async def test_markers_publish_profiles_changed(client, monkeypatch):  # noqa: F811
    pid = await _video_profile(client, [])
    sent = []
    monkeypatch.setattr(client.hub.bus, "publish", lambda t, d=None: sent.append(t))
    await client.put(f"/api/profiles/{pid}/markers", json=[])
    assert "profiles_changed" in sent


async def test_profile_put_without_markers_keeps_them(client):  # noqa: F811
    pid = await _video_profile(client, [])
    await client.put(f"/api/profiles/{pid}/markers", json=[{"id": "a", "t": 1}])
    body = (await client.get(f"/api/profiles/{pid}")).json()
    body.pop("markers")
    assert (await client.put(f"/api/profiles/{pid}", json=body)).status_code == 200
    assert len(await _markers(client, pid)) == 1


async def test_profile_put_with_markers_replaces_and_validates(client):  # noqa: F811
    pid = await _video_profile(client, [])
    await client.put(f"/api/profiles/{pid}/markers", json=[{"id": "a", "t": 1}])
    body = (await client.get(f"/api/profiles/{pid}")).json()
    body["markers"] = [{"id": "n", "t": 9}, {"id": "m", "t": 2}]
    assert (await client.put(f"/api/profiles/{pid}", json=body)).status_code == 200
    assert [m["id"] for m in await _markers(client, pid)] == ["m", "n"]
    body["markers"] = [{"id": "d", "t": 1}, {"id": "d", "t": 2}]
    assert (await client.put(f"/api/profiles/{pid}", json=body)).status_code == 422


async def test_duplicate_carries_markers_and_cue_fields(client):  # noqa: F811
    pid = await _video_profile(
        client, [{"start_s": 1, "state": {"bri": 5}, "label": "Boo", "color": "#123456"}]
    )
    await client.put(f"/api/profiles/{pid}/markers", json=[{"id": "m", "t": 2, "label": "x"}])
    r = await client.post(f"/api/profiles/{pid}/duplicate")
    assert r.status_code == 201
    out = r.json()
    assert out["markers"][0]["id"] == "m"
    assert out["cues"][0]["label"] == "Boo" and out["cues"][0]["color"] == "#123456"


async def test_export_import_keeps_markers_and_labels(client):  # noqa: F811
    pid = await _video_profile(
        client, [{"start_s": 1, "state": {"bri": 5}, "label": "Boo", "color": "#123456"}]
    )
    await client.put(f"/api/profiles/{pid}/markers", json=[{"id": "m", "t": 2, "label": "x"}])
    data = (await client.get(f"/api/profiles/{pid}/export")).json()
    r = await client.post("/api/profiles/import", json={"data": data})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["markers"] == [{"id": "m", "t": 2.0, "label": "x", "color": None}]
    assert out["cues"][0]["label"] == "Boo" and out["cues"][0]["color"] == "#123456"


async def test_import_old_export_without_markers(client):  # noqa: F811
    pid = await _video_profile(client, [{"start_s": 1, "state": {"bri": 5}}])
    data = (await client.get(f"/api/profiles/{pid}/export")).json()
    del data["profile"]["markers"]
    for c in data["cues"]:
        del c["label"], c["color"]
    r = await client.post("/api/profiles/import", json={"data": data})
    assert r.status_code == 201, r.text
    assert r.json()["markers"] == []
