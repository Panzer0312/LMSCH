"""'Recommended settings' checks against a board's /json/cfg, with one-click fixes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Check:
    id: str
    title: str
    ok: bool
    detail: str
    fix: dict[str, Any] | None  # body for POST /json/cfg


def run_checks(cfg: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    wifi = cfg.get("wifi", {}) if isinstance(cfg.get("wifi"), dict) else {}
    sleep = wifi.get("sleep")
    checks.append(
        Check(
            id="wifi_sleep",
            title="WiFi sleep disabled",
            ok=sleep is False,
            detail="WiFi power saving adds 100-300 ms of random latency to every command."
            if sleep is not False
            else "WiFi sleep is off.",
            fix={"wifi": {"sleep": False}} if sleep is not False else None,
        )
    )
    return checks
