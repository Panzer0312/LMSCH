"""Speaker check sound, rendered once with ffmpeg and cached."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def ensure_test_sound(data_dir: Path) -> Path:
    out = data_dir / "test-sound.wav"
    if out.exists():
        return out
    # Rising sweep 200->800 Hz over 1.5 s (phase = integral of frequency), then a 0.4 s low tone.
    sweep = "aevalsrc=0.5*sin(2*PI*(200*t+200*t*t/1.5)):d=1.5:s=44100"
    boo = "sine=frequency=110:duration=0.4:sample_rate=44100"
    tmp = out.with_suffix(".tmp.wav")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", sweep, "-f", "lavfi", "-i", boo,
        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1,afade=t=out:st=1.8:d=0.1[a]",
        "-map", "[a]", "-ac", "2", str(tmp),
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, err = await proc.communicate()
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(err.decode(errors="replace")[-300:])
    # Rename last so a crash never leaves a truncated file that counts as cached.
    tmp.replace(out)
    return out
