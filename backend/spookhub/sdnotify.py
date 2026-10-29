"""Minimal systemd sd_notify: READY/WATCHDOG over the NOTIFY_SOCKET datagram socket."""

from __future__ import annotations

import asyncio
import logging
import os
import socket

log = logging.getLogger(__name__)


def notify(msg: str) -> bool:
    """Send one notification; no-op (False) when not run under systemd or on failure."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]  # abstract namespace socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.settimeout(0.2)  # never stall the event loop on a wedged socket
            s.connect(addr)
            s.sendall(msg.encode())
    except OSError as exc:
        log.warning("sd_notify failed: %s", exc)
        return False
    return True


def watchdog_interval_s() -> float | None:
    """Half the unit's WatchdogSec, or None when the watchdog is not enabled."""
    pid = os.environ.get("WATCHDOG_PID")
    if pid and pid != str(os.getpid()):
        return None  # the watchdog is meant for another process
    try:
        usec = int(os.environ.get("WATCHDOG_USEC", ""))
    except ValueError:
        return None
    return usec / 2_000_000 if usec > 0 else None


async def watchdog_loop(interval_s: float) -> None:
    # Runs on the event loop on purpose: a blocked loop stops the pings and systemd restarts us.
    while True:
        notify("WATCHDOG=1")
        await asyncio.sleep(interval_s)
