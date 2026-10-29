"""Output discovery: DRM connectors from sysfs, audio sinks from mpv. Pure, testable."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_CONNECTOR = re.compile(r"^card(\d+)-(.+)$")


# Kernel-internal connectors that never carry a projector.
_SKIP = ("Writeback-", "Virtual-")


def _natural(name: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def drm_outputs(root: Path = Path("/sys/class/drm")) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        m = _CONNECTOR.match(d.name)
        if not m:
            continue
        if m.group(2).startswith(_SKIP):
            continue
        status = _read(d / "status") or "unknown"
        out.append(
            {
                "card": int(m.group(1)),
                "name": m.group(2),
                "status": status,
                "modes": list(dict.fromkeys(x for x in _read(d / "modes").splitlines() if x)),
                "enabled": _read(d / "enabled") == "enabled",
            }
        )
    out.sort(key=lambda o: (o["status"] != "connected", o["card"], _natural(o["name"])))
    return out


def parse_audio_devices(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"name": str(d["name"]), "description": str(d.get("description") or d["name"])}
        for d in raw
        if isinstance(d, dict) and d.get("name")
    ]
