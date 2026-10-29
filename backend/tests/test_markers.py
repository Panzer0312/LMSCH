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


async def test_markers_put_validation_and_survive_update(client):  # noqa: F811
    pid = await _video_profile(client, [])
    ms = [
        {"id": "b", "t": 5, "label": "late", "color": "#00ff00"},
        {"id": "a", "t": 1.5},
    ]
    r = await client.put(f"/api/profiles/{pid}/markers", json=ms)
    assert r.status_code == 200
    got = r.json()["markers"]
    assert [m["id"] for m in got] == ["a", "b"]
    assert got[0]["label"] == "" and got[0]["color"] is None
    for bad in (
        [{"id": "x", "t": -1}],
        [{"id": "x", "t": 1, "color": "nope"}],
        [{"id": "x", "t": 1}, {"id": "x", "t": 2}],
    ):
        assert (await client.put(f"/api/profiles/{pid}/markers", json=bad)).status_code == 422
    # Profile update without markers keeps them.
    body = (await client.get(f"/api/profiles/{pid}")).json()
    body.pop("markers")
    assert (await client.put(f"/api/profiles/{pid}", json=body)).status_code == 200
    assert len((await client.get(f"/api/profiles/{pid}")).json()["markers"]) == 2


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
