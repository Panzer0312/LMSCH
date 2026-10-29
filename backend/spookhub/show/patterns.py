"""Projector alignment and scanning patterns, rendered once with ffmpeg lavfi and cached.

Files live under <data_dir>/patterns/<id>-<w>x<h>.(png|mp4). All drawing is expressed relative to
the frame size so every pattern renders correctly at any output resolution.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
import weakref
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from spookhub.subproc import communicate

Kind = Literal["image", "video"]

VIDEO_SECONDS = 4
VIDEO_FPS = 30
# A 4 s 1080p clip renders in a few seconds; past this ffmpeg is stuck.
RENDER_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class Pattern:
    id: str
    label: str
    kind: Kind


PATTERNS: list[Pattern] = [
    Pattern("grid", "Grid", "image"),
    Pattern("corners", "Numbered corners + border", "image"),
    Pattern("circles", "Crosshair + circles", "image"),
    Pattern("edge_chase", "Edge chase", "video"),
    Pattern("scan_h", "Scan line, horizontal", "video"),
    Pattern("scan_v", "Scan line, vertical", "video"),
    Pattern("focus", "Focus (star + line pairs)", "image"),
    Pattern("grey_ramp", "Grey ramp", "image"),
    Pattern("checker", "Checkerboard", "image"),
    Pattern("white", "White", "image"),
    Pattern("black", "Black", "image"),
    Pattern("safe_area", "Safe area (mask check)", "image"),
]
_BY_ID = {p.id: p for p in PATTERNS}

# Per event loop (locks are loop-bound; tests run one loop per test).
_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


@cache
def _font() -> str | None:
    """Bold font file for drawtext, or None when drawtext/fontconfig is unavailable.

    Blocking (runs ffmpeg and fc-match): call it via asyncio.to_thread."""
    if not shutil.which("fc-match"):
        return None
    try:
        filters = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10
        ).stdout
        if " drawtext " not in filters:
            return None
        path = subprocess.run(
            ["fc-match", "-f", "%{file}", "DejaVu Sans:bold"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return path if path and Path(path).is_file() else None


def _esc(path: str) -> str:
    # Filtergraph quoting: backslash-escape the characters that end or split an option value.
    return "'" + path.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:") + "'"


def _gray(expr: str) -> str:
    return f"format=gray,geq=lum='{expr}',format=rgb24"


def _corner_labels(font: str | None) -> list[str]:
    m = "ih/14"  # inset from the frame edge
    # TL, TR, BR, BL: matches the corner order of the keystone editor.
    spots = [
        (m, m, "1", False),
        (f"iw-{m}-ih/8", m, "2", True),
        (f"iw-{m}-ih/8", f"ih-{m}-ih/8", "3", True),
        (m, f"ih-{m}-ih/8", "4", False),
    ]
    colors = ["0xe5383b", "0x7ee36b", "0x3a86ff", "0xffbe0b"]
    out = []
    for (x, y, n, right), c in zip(spots, colors, strict=True):
        # Filled corner square in a distinct colour, so corners are identifiable even unreadable.
        out.append(f"drawbox=x={x}:y={y}:w=ih/8:h=ih/8:t=fill:c={c}")
        # Labels sit on the inner side of the square so they never leave the frame.
        if font:
            tx = f"{x}-ih/40-tw" if right else f"{x}+ih/8+ih/40"
            # drawtext names the frame size W/H instead of iw/ih.
            tx = tx.replace("iw", "W").replace("ih", "H")
            ty = f"{y}-ih/60".replace("ih", "H")
            out.append(
                f"drawtext=fontfile={_esc(font)}:text={n}:fontcolor=white:fontsize=H/5:"
                f"x={tx}:y={ty}"
            )
        else:
            # No usable font: count the corner with pips beside the square.
            for i in range(int(n)):
                px = f"{x}-ih/40-{i + 1}*ih/22" if right else f"{x}+ih/8+ih/40+{i}*ih/22"
                out.append(f"drawbox=x={px}:y={y}+ih/32:w=ih/30:h=ih/16:t=fill:c=white")
    return out


def _filters(pid: str, font: str | None = None) -> tuple[str, str]:
    """(lavfi source colour, -vf chain) for a pattern; `font` from _font() for labels."""
    t = "max(2\\,ih/270)"  # line thickness that survives downscaling
    if pid == "grid":
        return "black", ",".join(
            [
                "drawgrid=w=iw/16:h=ih/9:t=max(1\\,ih/540):c=white@0.55",
                f"drawgrid=w=iw/4:h=ih/4:t={t}:c=0xff7a1a@0.9",
                # Border and centre cross make keystone skew obvious.
                "drawbox=x=0:y=0:w=iw:h=ih:t=ih/135:c=0x7ee36b",
                f"drawbox=x=iw/2-{t}/2:y=0:w={t}:h=ih:t=fill:c=0xe5383b",
                f"drawbox=x=0:y=ih/2-{t}/2:w=iw:h={t}:t=fill:c=0xe5383b",
                f"drawbox=x=iw/2-ih/18:y=ih/2-ih/18:w=ih/9:h=ih/9:t={t}:c=white",
            ]
        )
    if pid == "corners":
        return "black", ",".join(
            ["drawbox=x=0:y=0:w=iw:h=ih:t=ih/90:c=white", *_corner_labels(font)]
        )
    if pid == "circles":
        rings = "lt(abs(mod(hypot(X-W/2,Y-H/2)+H/32,H/8)-H/32),max(1,H/360))"
        cross = "lt(abs(X-W/2),max(1,H/360))+lt(abs(Y-H/2),max(1,H/360))"
        return "black", _gray(f"255*gt({rings}+{cross},0)")
    if pid == "focus":
        star = "mod(floor((atan2(Y-H/2,X-W/2)+PI)/(PI/36)),2)"
        vlines = "mod(floor(X/2),2)"
        hlines = "mod(floor(Y/2),2)"
        outer = f"if(gt(abs(X-W/2)/W,abs(Y-H/2)/H),{vlines},{hlines})"
        return "black", _gray(f"255*if(lt(hypot(X-W/2,Y-H/2),H*0.4),{star},{outer})")
    if pid == "grey_ramp":
        # Top half 16 steps (crushed blacks show as merged steps), bottom half smooth.
        return "black", _gray("if(lt(Y,H/2),floor(X*16/W)*17,X*255/(W-1))")
    if pid == "checker":
        return "black", _gray("255*mod(floor(X/(H/9))+floor(Y/(H/9)),2)")
    if pid == "white":
        return "white", "null"
    if pid == "black":
        return "black", "null"
    if pid == "safe_area":
        # Plain mid-grey: the warp shader's mask makes the usable area's edges visible.
        return "0x808080", "null"
    p = f"mod(t\\,{VIDEO_SECONDS})/{VIDEO_SECONDS}"  # 0..1 phase, loops seamlessly
    if pid == "edge_chase":
        bt = "ih/36"
        bl = "iw/6"
        return "black", ",".join(
            [
                "drawbox=x=0:y=0:w=iw:h=ih:t=max(1\\,ih/540):c=white@0.4",
                f"drawbox=x={p}*iw-{bl}/2:y=0:w={bl}:h={bt}:t=fill:c=0xe5383b",
                f"drawbox=x=iw-{bt}:y={p}*ih-{bl}/2:w={bt}:h={bl}:t=fill:c=0x7ee36b",
                f"drawbox=x=iw-{p}*iw-{bl}/2:y=ih-{bt}:w={bl}:h={bt}:t=fill:c=0x3a86ff",
                f"drawbox=x=0:y=ih-{p}*ih-{bl}/2:w={bt}:h={bl}:t=fill:c=0xffbe0b",
            ]
        )
    if pid == "scan_h":
        return "black", f"drawbox=x=0:y={p}*ih:w=iw:h=ih/90:t=fill:c=white"
    if pid == "scan_v":
        return "black", f"drawbox=x={p}*iw:y=0:w=ih/90:h=ih:t=fill:c=white"
    raise KeyError(pid)


def path_for(data_dir: Path, pid: str, size: tuple[int, int]) -> Path:
    ext = "mp4" if _BY_ID[pid].kind == "video" else "png"
    return data_dir / "patterns" / f"{pid}-{size[0]}x{size[1]}.{ext}"


async def _render(pid: str, size: tuple[int, int], out: Path) -> None:
    font = await asyncio.to_thread(_font) if pid == "corners" else None
    color, vf = _filters(pid, font)
    w, h = size
    src = f"color=c={color}:s={w}x{h}:r={VIDEO_FPS}"
    if _BY_ID[pid].kind == "video":
        enc = [
            "-t", str(VIDEO_SECONDS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4",
        ]  # fmt: skip
    else:
        enc = ["-frames:v", "1", "-f", "image2", "-c:v", "png"]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-vf", vf, *enc, str(out),
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        # Returns only once ffmpeg has exited (killed on timeout/cancel), so the caller may
        # unlink the temp file right after.
        _, err = await communicate(proc, RENDER_TIMEOUT_S)
    except TimeoutError:
        raise RuntimeError(f"ffmpeg timed out after {RENDER_TIMEOUT_S:.0f} s") from None
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="replace").strip()[-300:])


async def ensure(data_dir: Path, id: str, size: tuple[int, int] = (1920, 1080)) -> Path:  # noqa: A002
    """Path of the rendered pattern, generating it on first use. KeyError on unknown id."""
    if id not in _BY_ID:
        raise KeyError(id)
    # yuv420p and the grid maths need even dimensions.
    size = (max(2, size[0] // 2 * 2), max(2, size[1] // 2 * 2))
    out = path_for(data_dir, id, size)
    if out.exists():
        return out
    key = out.name
    lock = _locks.setdefault(asyncio.get_running_loop(), {}).setdefault(key, asyncio.Lock())
    async with lock:
        if out.exists():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name so a second process rendering the same pattern never shares the file;
        # os.replace makes the finished file appear atomically.
        tmp = out.with_name(f".{out.stem}.{uuid.uuid4().hex}{out.suffix}")
        try:
            await _render(id, size, tmp)
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
    return out
