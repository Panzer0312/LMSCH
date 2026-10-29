"""Projector alignment grid for keystone/focus setup; the grid from `show.patterns`."""

from __future__ import annotations

from pathlib import Path

from spookhub.show import patterns


async def ensure_pattern(data_dir: Path) -> Path:
    return await patterns.ensure(data_dir, "grid")
