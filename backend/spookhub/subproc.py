"""Subprocess helpers shared by the ffmpeg/ffprobe wrappers."""

from __future__ import annotations

import asyncio
import contextlib


async def communicate(proc: asyncio.subprocess.Process, timeout: float) -> tuple[bytes, bytes]:
    """`proc.communicate()` with a deadline (TimeoutError).

    On timeout or cancellation the process is killed and reaped before the error propagates, so
    an abandoned request never leaves ffmpeg running or writing into a file being cleaned up."""
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
    except BaseException:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            # Shielded: a cancelled caller still waits for the exit, the reap itself is not lost.
            await asyncio.shield(proc.wait())
        raise
    return out or b"", err or b""
