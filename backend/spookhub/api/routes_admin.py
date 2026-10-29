from __future__ import annotations

import os
import signal
import time
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from spookhub import backup
from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.show.engine import SetupBusy
from spookhub.show.testpattern import ensure_pattern

router = APIRouter(prefix="/api", tags=["admin"])
HubDep = Annotated[Hub, Depends(get_hub)]
MAX_BACKUP = 200 * 1024 * 1024


class FlagIn(BaseModel):
    on: bool


@router.post("/show/test_pattern")
async def test_pattern(body: FlagIn, hub: HubDep) -> dict[str, bool]:
    if body.on:
        try:
            img = await ensure_pattern(hub.cfg.paths.data_dir)
        except RuntimeError as e:
            raise HTTPException(500, f"could not render test pattern: {e}") from e
        try:
            await hub.engine.set_test_pattern(True, str(img))
        except SetupBusy as e:
            raise HTTPException(409, str(e)) from e
    else:
        await hub.engine.set_test_pattern(False)
    return {"ok": True}


@router.get("/backup")
def download_backup(hub: HubDep) -> Response:
    data = backup.make_backup(hub.cfg.paths.db_path)
    name = time.strftime("spookhub-backup-%Y%m%d-%H%M.zip")
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _restart() -> None:
    # Graceful shutdown; systemd (Restart=always) starts us again and the staged DB is swapped in.
    time.sleep(0.5)
    os.kill(os.getpid(), signal.SIGTERM)


@router.post("/backup/restore")
async def restore_backup(file: UploadFile, hub: HubDep, bg: BackgroundTasks) -> dict[str, Any]:
    data = await file.read(MAX_BACKUP + 1)
    if len(data) > MAX_BACKUP:
        raise HTTPException(413, "backup too large")
    try:
        notes = backup.stage_restore(data, hub.cfg.paths.db_path)
    except backup.BackupError as e:
        raise HTTPException(422, str(e)) from e
    hub.bus.event("warn", "Backup restored, restarting…")
    bg.add_task(_restart)
    return {"ok": True, "notes": notes}
