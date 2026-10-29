"""Board registry: runtime state, health checks and desired-state reconciliation.

Every command updates a board's *desired state* first, then tries delivery. Whenever a board
comes back (reconnect or new IP matched by MAC) the full desired state is re-sent, so missed
commands heal themselves.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from spookhub import db
from spookhub.boards.client import WledClient, WledError, build_payload
from spookhub.boards.queue import Command, LatestWinsQueue
from spookhub.config import TimingCfg
from spookhub.events import EventBus
from spookhub.models import OFF_STATE, Board, LightState
from spookhub.tasks import spawn

log = logging.getLogger(__name__)

IDENTIFY_STATE = LightState(on=True, bri=255, fx=0, colors=[(255, 255, 255)], transition_ms=0)
LAST_SEEN_PERSIST_S = 60.0
FALLBACK_PRESET = 250
FALLBACK_NAME = "Spookhub fallback"
FALLBACK_WRITE_TIMEOUT_S = 5.0  # flash writes on the board are slow


class BoardStatus(enum.StrEnum):
    unknown = "unknown"
    online = "online"
    degraded = "degraded"
    offline = "offline"


def normalize_mac(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").lower()


@dataclass
class BoardRuntime:
    mac: str
    ip: str
    name: str = ""
    adopted: bool = False
    firmware: str = ""
    led_count: int = 0
    rssi: int | None = None
    signal: int | None = None
    last_seen: float | None = None
    status: BoardStatus = BoardStatus.unknown
    latency_ms: float | None = None
    offset_ms: float = 0.0
    failures: int = 0
    live: dict[str, Any] = field(default_factory=dict)
    seg_ids: list[int] = field(default_factory=lambda: [0])
    segments: list[dict[str, Any]] = field(default_factory=list)  # [{id, name, start, stop}]
    effects: list[str] = field(default_factory=list)
    palettes: list[str] = field(default_factory=list)
    fxdata: list[str] = field(default_factory=list)
    presets: dict[str, Any] = field(default_factory=dict)
    show_state: LightState | None = None
    # Segment id -> state for segments whose own segment cue is active. Sent inside
    # show_state's payload; segments not listed follow show_state.
    show_segs: dict[int, LightState] = field(default_factory=dict)
    manual: LightState | None = None
    identify_until: float = 0.0
    # Held by the preflight test-all; separate from identify so neither ends the other.
    hold_white: bool = False
    # Last WiFi-sleep reading from the recommendations check; None = never checked.
    wifi_sleep: bool | None = None
    wifi_sleep_at: float | None = None  # time.time() of that reading
    queue: LatestWinsQueue | None = None
    fallback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    saving_fallback: bool = False  # push() is a no-op meanwhile; save_fallback replays it
    _persisted_seen: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "mac": self.mac,
            "ip": self.ip,
            "name": self.name or self.mac,
            "adopted": self.adopted,
            "firmware": self.firmware,
            "led_count": self.led_count,
            "rssi": self.rssi,
            "signal": self.signal,
            "last_seen": self.last_seen,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
            "offset_ms": self.offset_ms,
            "live": self.live,
            "effects": self.effects,
            "palettes": self.palettes,
            "fxdata": self.fxdata,
            "segments": self.segments,
            "presets": {
                k: v.get("n", f"Preset {k}")
                for k, v in self.presets.items()
                if k != "0" and isinstance(v, dict)
            },
        }


class BoardManager:
    def __init__(
        self,
        timing: TimingCfg,
        bus: EventBus,
        wled: WledClient,
        master: Callable[[], float] = lambda: 1.0,
        health_interval: Callable[[], float] | None = None,
    ) -> None:
        self.timing = timing
        self.bus = bus
        self.wled = wled
        self.master = master
        self.health_interval = health_interval or (lambda: timing.health_interval_s)
        self.boards: dict[str, BoardRuntime] = {}
        self.blackout = False
        self._task: asyncio.Task[None] | None = None
        self._checking: set[str] = set()

    # ----- lifecycle -----

    def load_from_db(self) -> None:
        with db.session() as s:
            for row in s.exec(select(Board)).all():
                b = BoardRuntime(
                    mac=row.mac,
                    ip=row.ip,
                    name=row.name,
                    adopted=row.adopted,
                    firmware=row.firmware,
                    led_count=row.led_count,
                    offset_ms=row.offset_ms or 0.0,
                    effects=list(row.effects or []),
                    palettes=list(row.palettes or []),
                    fxdata=list(row.fxdata or []),
                    segments=list(row.segments or []),
                    presets=dict(row.presets or {}),
                    last_seen=row.last_seen.timestamp() if row.last_seen else None,
                )
                if b.segments:
                    b.seg_ids = [int(sg["id"]) for sg in b.segments]
                self._attach_queue(b)
                self.boards[b.mac] = b

    async def start(self) -> None:
        for b in self.boards.values():
            assert b.queue
            b.queue.start()
        self._task = asyncio.create_task(self._health_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for b in self.boards.values():
            if b.queue:
                await b.queue.stop()

    def _attach_queue(self, b: BoardRuntime) -> None:
        async def send(cmd: Command, timeout: float) -> float:
            return await self.wled.set_state(b.ip, cmd.payload, timeout)

        def on_result(cmd: Command, ok: bool, ms: float | None) -> None:
            if ok and ms is not None:
                self._record_latency(b, ms)

        b.queue = LatestWinsQueue(
            send=send,
            cue_timeout_s=self.timing.cue_timeout_s,
            command_timeout_s=self.timing.command_timeout_s,
            backoff_max_s=self.timing.backoff_max_s,
            on_result=on_result,
        )

    # ----- queries -----

    def adopted(self) -> list[BoardRuntime]:
        return [b for b in self.boards.values() if b.adopted]

    def adopted_macs(self) -> list[str]:
        return [b.mac for b in self.adopted()]

    def snapshot(self) -> list[dict[str, Any]]:
        return [b.snapshot() for b in self.boards.values()]

    def desired(self, b: BoardRuntime) -> LightState | None:
        if self.blackout:
            return OFF_STATE
        if b.hold_white or b.identify_until > time.monotonic():
            return IDENTIFY_STATE
        if b.show_state is None:
            return b.manual
        return b.show_state.merged(b.manual)

    def desired_segs(self, b: BoardRuntime) -> dict[int, LightState]:
        """Segment states on top of desired(); only while the show state is what is shown."""
        if self.blackout or b.hold_white or b.identify_until > time.monotonic():
            return {}
        if b.show_state is None:
            return {}
        known = set(b.seg_ids)
        # Manual (dashboard) control is whole-board and wins over segment cues too.
        return {i: st.merged(b.manual) for i, st in b.show_segs.items() if i in known}

    def latency_comp_ms(self, mac: str) -> float:
        b = self.boards.get(mac)
        if not b:
            return 0.0
        # One-way ≈ half the round trip; WLED applies on receipt. The cap limits only this
        # measured part: the manual offset is the user's decision.
        measured = 0.0
        if b.latency_ms is not None:
            measured = min(b.latency_ms / 2.0, self.timing.latency_comp_cap_ms)
        return measured + b.offset_ms

    # ----- commands -----

    def push(self, mac: str, critical: bool = False) -> None:
        """Deliver the current desired state for a board (no-op if nothing desired)."""
        b = self.boards.get(mac)
        if not b or not b.adopted or b.queue is None:
            return
        # Blackout is the safety control: it must reach the board even mid fallback save
        # (the save aborts at its next step and replays this afterwards).
        if b.saving_fallback and not self.blackout:
            return
        state = self.desired(b)
        if state is None:
            return
        fallback = int(b.live.get("bri") or 128)
        payload = build_payload(
            state,
            presets=b.presets,
            seg_ids=b.seg_ids,
            master=self.master(),
            fallback_bri=fallback,
            seg_states=self.desired_segs(b),
        )
        if payload:
            b.queue.submit(payload, critical)

    def push_all(self, critical: bool = False) -> None:
        for mac in self.adopted_macs():
            self.push(mac, critical)

    def set_show_state(
        self,
        mac: str,
        state: LightState | None,
        critical: bool,
        segs: dict[int, LightState] | None = None,
    ) -> None:
        b = self.boards.get(mac)
        if not b:
            return
        b.show_state = state
        b.show_segs = dict(segs or {})
        self.push(mac, critical)

    def clear_show_states(self) -> None:
        for b in self.boards.values():
            b.show_state = None
            b.show_segs = {}
            b.manual = None

    def set_manual(self, mac: str, state: LightState) -> None:
        b = self.boards[mac]
        b.manual = (b.manual or LightState()).merged(state)
        self.push(mac)

    def clear_manual(self, mac: str) -> None:
        b = self.boards[mac]
        b.manual = None
        self.push(mac)

    def set_blackout(self, on: bool) -> None:
        self.blackout = on
        self.push_all()

    def hold_identify(self) -> None:
        """White on every adopted board until release_identify (same look as identify)."""
        for b in self.adopted():
            b.hold_white = True
        self.push_all()

    def release_identify(self) -> None:
        for b in self.boards.values():
            b.hold_white = False
        self.push_all()

    async def identify(self, mac: str, seconds: float = 2.0) -> None:
        b = self.boards[mac]
        b.identify_until = time.monotonic() + seconds
        self.push(mac)
        await asyncio.sleep(seconds + 0.05)
        self.push(mac)

    async def save_fallback(self, mac: str, state: LightState | None) -> str | None:
        """Make `state` the board's boot state (WLED preset 250 + boot preset). None = ok.

        Sent directly, not through the queue: psave must follow the state it stores. Desired-state
        pushes for this board are deferred meanwhile and replayed afterwards, so the board returns
        to what the show wants (master applied) instead of keeping the saved look.
        """
        b = self.boards[mac]
        if state is None:
            return "no base state"
        # Full brightness: master/cap are show-night knobs, not part of the boot look.
        payload = build_payload(state, presets=b.presets, seg_ids=b.seg_ids)
        if not payload:
            return "no base state"
        async with b.fallback_lock:
            b.saving_fallback = True
            ok = False
            try:
                if b.queue is not None:
                    try:
                        async with asyncio.timeout(FALLBACK_WRITE_TIMEOUT_S):
                            while not b.queue.idle:  # noqa: ASYNC110  (queue has no idle event)
                                await asyncio.sleep(0.02)
                    except TimeoutError:
                        return "board busy"
                if self.blackout:
                    return "blackout active"
                await self.wled.set_state(b.ip, payload, FALLBACK_WRITE_TIMEOUT_S)
                if self.blackout:
                    return "blackout active"
                await self.wled.set_state(
                    b.ip,
                    {"psave": FALLBACK_PRESET, "n": FALLBACK_NAME, "ib": True, "sb": True},
                    FALLBACK_WRITE_TIMEOUT_S,
                )
                if self.blackout:
                    return "blackout active"
                await self.wled.set_cfg(
                    b.ip, {"def": {"ps": FALLBACK_PRESET, "on": True}}, FALLBACK_WRITE_TIMEOUT_S
                )
                ok = True
            except WledError as e:
                return str(e)
            finally:
                b.saving_fallback = False
                self.push(mac)
                if ok:
                    spawn(self.refresh_meta(mac))  # preset cache now includes 250
        return None

    # ----- health & reconciliation -----

    def _record_latency(self, b: BoardRuntime, ms: float) -> None:
        b.latency_ms = ms if b.latency_ms is None else b.latency_ms * 0.8 + ms * 0.2

    async def _health_loop(self) -> None:
        while True:
            t0 = time.monotonic()
            await asyncio.gather(
                *(self.check(b) for b in list(self.boards.values())), return_exceptions=True
            )
            await asyncio.sleep(max(0.2, self.health_interval() - (time.monotonic() - t0)))

    async def check(self, b: BoardRuntime) -> None:
        if b.mac in self._checking:
            return
        self._checking.add(b.mac)
        try:
            await self._check(b)
        finally:
            self._checking.discard(b.mac)

    async def _check(self, b: BoardRuntime) -> None:
        prev = b.status
        try:
            data, ms = await self.wled.state_info(b.ip, self.timing.health_timeout_s)
            info = data.get("info", {})
            mac = normalize_mac(str(info.get("mac", "")))
            if mac and mac != b.mac:
                # DHCP handed this IP to a different board; ours is elsewhere now.
                raise WledError(f"ip {b.ip} now belongs to {mac}")
        except WledError as e:
            b.failures += 1
            if b.failures >= self.timing.offline_after_failures:
                b.status = BoardStatus.offline
            elif prev != BoardStatus.unknown:
                b.status = BoardStatus.degraded
            else:
                b.status = BoardStatus.offline if b.failures > 1 else BoardStatus.unknown
            if b.status != prev:
                log.info("board %s %s -> %s (%s)", b.mac, prev, b.status, e)
                if b.status == BoardStatus.offline:
                    self.bus.event("warn", f"{b.name or b.mac} offline", kind="board", mac=b.mac)
                self._publish(b)
            return

        self._record_latency(b, ms)
        b.failures = 0
        self._update_from_si(b, data)
        b.status = (
            BoardStatus.degraded
            if (b.latency_ms or 0) > self.timing.degraded_latency_ms
            else BoardStatus.online
        )
        if prev in (BoardStatus.offline, BoardStatus.unknown) and b.adopted:
            if not b.effects or not b.presets or not b.fxdata:
                spawn(self.refresh_meta(b.mac))
            self._reconcile(b, reconnect=prev == BoardStatus.offline)
        self._maybe_persist_seen(b)
        self._publish(b)

    def _reconcile(self, b: BoardRuntime, reconnect: bool, reason: str = "reconnected") -> None:
        if self.desired(b) is None:
            if reconnect:
                self.bus.event("info", f"{b.name or b.mac} {reason}", kind="board", mac=b.mac)
            return
        self.push(b.mac)
        msg = f"{b.name or b.mac} {reason}, state restored"
        log.info(msg)
        if reconnect:
            self.bus.event("info", msg, kind="board", mac=b.mac)

    def _update_from_si(self, b: BoardRuntime, data: dict[str, Any]) -> None:
        info = data.get("info", {})
        state = data.get("state", {})
        b.last_seen = time.time()
        b.firmware = str(info.get("ver", b.firmware))
        b.led_count = int(info.get("leds", {}).get("count", b.led_count) or 0)
        wifi = info.get("wifi", {})
        b.rssi = wifi.get("rssi")
        b.signal = wifi.get("signal")
        segs = state.get("seg", [])
        if isinstance(segs, list) and segs:
            b.seg_ids = [int(s.get("id", i)) for i, s in enumerate(segs)]
            main = segs[0]
            segments = [
                {
                    "id": int(s.get("id", i)),
                    "name": str(s.get("n") or ""),
                    "start": int(s.get("start", 0) or 0),
                    "stop": int(s.get("stop", 0) or 0),
                }
                for i, s in enumerate(segs)
            ]
            if segments != b.segments:
                b.segments = segments
                self._persist(b)
        else:
            main = {}
        b.live = {
            "on": state.get("on"),
            "bri": state.get("bri"),
            "fx": main.get("fx"),
            "pal": main.get("pal"),
            "col": main.get("col"),
            "sx": main.get("sx"),
            "ix": main.get("ix"),
            "ps": state.get("ps"),
        }

    def _maybe_persist_seen(self, b: BoardRuntime) -> None:
        now = time.time()
        if now - b._persisted_seen < LAST_SEEN_PERSIST_S:
            return
        b._persisted_seen = now
        self._persist(b)

    def _persist(self, b: BoardRuntime) -> None:
        with db.session() as s:
            row = s.get(Board, b.mac) or Board(mac=b.mac, ip=b.ip)
            row.ip = b.ip
            row.name = b.name
            row.adopted = b.adopted
            row.firmware = b.firmware
            row.led_count = b.led_count
            row.offset_ms = b.offset_ms
            row.effects = b.effects
            row.palettes = b.palettes
            row.fxdata = b.fxdata
            row.segments = b.segments
            row.presets = b.presets
            if b.last_seen:
                row.last_seen = datetime.fromtimestamp(b.last_seen, UTC)
            s.add(row)
            s.commit()

    def _publish(self, b: BoardRuntime) -> None:
        self.bus.publish("board", b.snapshot())

    # ----- discovery / management -----

    async def probe(self, ip: str, source: str) -> BoardRuntime | None:
        """Ask an IP for /json/info; register or re-locate the board by MAC."""
        try:
            info, _ = await self.wled.info(ip, self.timing.scan_timeout_s * 2)
        except WledError:
            return None
        mac = normalize_mac(str(info.get("mac", "")))
        if not mac:
            return None
        return self.found(mac, ip, info, source)

    def found(self, mac: str, ip: str, info: dict[str, Any], source: str) -> BoardRuntime:
        b = self.boards.get(mac)
        if b is None:
            b = BoardRuntime(mac=mac, ip=ip, name=str(info.get("name", "")))
            b.firmware = str(info.get("ver", ""))
            b.led_count = int(info.get("leds", {}).get("count", 0) or 0)
            self._attach_queue(b)
            assert b.queue
            b.queue.start()
            self.boards[mac] = b
            self._persist(b)
            self.bus.event("info", f"Discovered {b.name or mac} at {ip} ({source})", mac=mac)
            self._publish(b)
            spawn(self.check(b))
            return b
        if b.ip != ip:
            old = b.ip
            b.ip = ip
            self._persist(b)
            log.info("board %s moved %s -> %s via %s", mac, old, ip, source)
            if b.adopted:
                self._reconcile(b, reconnect=True, reason=f"found at new IP {ip}")
            self._publish(b)
            spawn(self.check(b))
        return b

    async def adopt(self, mac: str, name: str) -> BoardRuntime:
        b = self.boards[mac]
        b.adopted = True
        b.name = name or b.name
        self._persist(b)
        await self.refresh_meta(mac)
        self.bus.event("info", f"Adopted {b.name}", mac=mac)
        self.bus.publish("boards_changed")
        return b

    def rename(self, mac: str, name: str) -> None:
        b = self.boards[mac]
        b.name = name
        self._persist(b)
        self._publish(b)

    def set_offset(self, mac: str, offset_ms: float) -> None:
        b = self.boards[mac]
        b.offset_ms = offset_ms
        self._persist(b)
        self._publish(b)

    async def remove(self, mac: str) -> None:
        b = self.boards.pop(mac)
        if b.queue:
            await b.queue.stop()
        with db.session() as s:
            row = s.get(Board, mac)
            if row:
                s.delete(row)
                s.commit()
        self.bus.publish("board_removed", {"mac": mac})
        self.bus.publish("boards_changed")

    async def refresh_meta(self, mac: str) -> None:
        b = self.boards[mac]
        try:
            fx, pal, ps = await asyncio.gather(
                self.wled.effects(b.ip), self.wled.palettes(b.ip), self.wled.presets(b.ip)
            )
        except WledError as e:
            log.warning("refresh meta %s failed: %s", mac, e)
            return
        # Older WLED builds have no /json/fxdata: effects then just lack slider metadata.
        try:
            fxd = await self.wled.fxdata(b.ip)
        except WledError:
            fxd = []
        b.effects, b.palettes, b.presets, b.fxdata = fx, pal, ps, fxd
        self._persist(b)
        self._publish(b)
