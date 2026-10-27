"""M3: PIN, trigger token, backups, export/import, recommendations, test pattern, transcode."""

import asyncio
import io
import shutil
import subprocess
import zipfile

import pytest

from spookhub import backup
from tests.test_api import client  # noqa: F401  (fixture)


async def test_pin_protects_api_and_trigger_token(client):  # noqa: F811
    assert (await client.get("/api/auth/status")).json() == {"pin_enabled": False}
    r = await client.put("/api/auth/pin", json={"pin": "1031"})
    assert r.json() == {"pin_enabled": True}
    # The client that set the PIN got a session cookie and stays logged in.
    assert (await client.get("/api/boards")).status_code == 200
    client.cookies.clear()
    assert (await client.get("/api/boards")).status_code == 401
    assert (await client.post("/api/blackout", json={"on": True})).status_code == 401
    assert (await client.post("/api/auth/login", json={"pin": "0000"})).status_code == 401
    assert (await client.post("/api/auth/login", json={"pin": "1031"})).status_code == 200
    assert (await client.get("/api/boards")).status_code == 200

    # External trigger bypasses the PIN but honours its own token.
    client.cookies.clear()
    assert (await client.post("/api/trigger")).status_code == 200
    await client.post("/api/auth/login", json={"pin": "1031"})
    await client.put("/api/auth/trigger_token", json={"token": "s3cret"})
    client.cookies.clear()
    assert (await client.post("/api/trigger")).status_code == 401
    r = await client.post("/api/trigger", headers={"X-Trigger-Token": "s3cret"})
    assert r.status_code == 200
    assert (await client.post("/api/trigger?token=s3cret")).status_code == 200


async def test_backup_roundtrip(client, cfg):  # noqa: F811
    await client.post("/api/profiles", json={"name": "Keep me"})
    r = await client.get("/api/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "spookhub.db" in names and backup.MANIFEST in names
    notes = backup.stage_restore(r.content, cfg.paths.db_path)
    assert notes and cfg.paths.db_path.with_suffix(".restore").exists()
    with pytest.raises(backup.BackupError):
        backup.stage_restore(b"not a zip", cfg.paths.db_path)


async def test_restore_swaps_db_on_startup(cfg, tmp_path):
    db_path = tmp_path / "x.db"
    db_path.write_text("old")
    db_path.with_suffix(".restore").write_text("new")
    assert backup.apply_staged_restore(db_path)
    assert db_path.read_text() == "new"
    assert db_path.with_suffix(".before-restore").read_text() == "old"


async def test_export_import(client, farm):  # noqa: F811
    sim = await farm.add(0)
    mac = (await client.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})).json()["mac"]
    p = (
        await client.post(
            "/api/profiles",
            json={
                "name": "Spooky",
                "selection": {"all": False, "macs": [mac]},
                "base_state": {"bri": 9},
            },
        )  # fmt: skip
    ).json()
    exp = await client.get(f"/api/profiles/{p['id']}/export")
    assert "attachment" in exp.headers["content-disposition"]
    data = exp.json()
    assert data["format"] == "spookhub-profile" and data["profile"]["name"] == "Spooky"
    imp = await client.post("/api/profiles/import", json={"data": data})
    assert imp.status_code == 201
    assert imp.json()["base_state"]["bri"] == 9
    assert imp.json()["selection"]["macs"] == [mac]

    data["profile"]["type"] = "video"
    data["media_filename"] = "missing.mp4"
    r = await client.post("/api/profiles/import", json={"data": data})
    assert r.status_code == 422 and "missing.mp4" in r.json()["detail"]
    r = await client.post("/api/profiles/import", json={"data": {"format": "nope"}})
    assert r.status_code == 422


async def test_recommended_settings(client, farm):  # noqa: F811
    sim = await farm.add(0)
    mac = (await client.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})).json()["mac"]
    checks = (await client.get(f"/api/boards/{mac}/recommendations")).json()
    sleep = next(c for c in checks if c["id"] == "wifi_sleep")
    assert not sleep["ok"] and sleep["fixable"]
    r = await client.post(f"/api/boards/{mac}/recommendations/wifi_sleep/apply")
    assert r.status_code == 200
    assert sim.cfg["wifi"]["sleep"] is False
    checks = (await client.get(f"/api/boards/{mac}/recommendations")).json()
    assert all(c["ok"] for c in checks)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_test_pattern_toggle(client):  # noqa: F811
    assert (await client.post("/api/show/test_pattern", json={"on": True})).status_code == 200
    st = (await client.get("/api/state")).json()["show"]
    assert st["test_pattern"] and st["phase"] == "test_pattern"
    assert (await client.post("/api/show/trigger")).json()["ok"] is False
    await client.post("/api/show/test_pattern", json={"on": False})
    assert (await client.get("/api/state")).json()["show"]["test_pattern"] is False


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_transcode_keeps_media_id(client, tmp_path):  # noqa: F811
    src = tmp_path / "old.avi"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-t", "2", "-c:v", "mpeg4", str(src)],
        check=True,
    )  # fmt: skip
    with src.open("rb") as f:
        m = (await client.post("/api/media", files={"file": ("old.avi", f)})).json()
    assert m["codec"] == "mpeg4" and m["needs_transcode"]
    assert (await client.post(f"/api/media/{m['id']}/transcode")).status_code == 202

    async def codec() -> str:
        items = (await client.get("/api/media")).json()
        return next(i for i in items if i["id"] == m["id"])["codec"]

    for _ in range(200):
        if await codec() == "h264":
            break
        await asyncio.sleep(0.05)
    items = (await client.get("/api/media")).json()
    after = next(i for i in items if i["id"] == m["id"])
    assert after["codec"] == "h264" and not after["needs_transcode"]
    assert after["transcode"] is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_transcode_odd_dimensions(tmp_path):
    # Screencasts often have odd sizes; libx264 with yuv420p refuses those.
    from spookhub.media.transcode import transcode

    src, dest = tmp_path / "cast.webm", tmp_path / "cast.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=321x241:rate=25",
         "-t", "1", "-c:v", "libvpx-vp9", str(src)],
        check=True,
    )  # fmt: skip
    await transcode(src, dest, 1.0, lambda _: None)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(dest)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    assert out == "320,240"
