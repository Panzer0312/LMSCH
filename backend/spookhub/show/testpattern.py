"""Projector alignment grid for keystone/focus setup, rendered once with ffmpeg and cached."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def ensure_pattern(data_dir: Path) -> Path:
    out = data_dir / "test-pattern.png"
    if out.exists():
        return out
    vf = ",".join(
        [
            "drawgrid=w=120:h=120:t=2:c=white@0.55",
            "drawgrid=w=480:h=270:t=4:c=0xff7a1a@0.9",
            # Border and centre cross make keystone skew obvious.
            "drawbox=x=0:y=0:w=iw:h=ih:t=8:c=0x7ee36b",
            "drawbox=x=iw/2-2:y=0:w=4:h=ih:t=fill:c=0xe5383b",
            "drawbox=x=0:y=ih/2-2:w=iw:h=4:t=fill:c=0xe5383b",
            "drawbox=x=iw/2-60:y=ih/2-60:w=120:h=120:t=4:c=white",
        ]
    )
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=1920x1080",
        "-vf", vf, "-frames:v", "1", str(out),
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="replace")[-300:])
    return out
