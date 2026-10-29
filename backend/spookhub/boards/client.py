"""WLED HTTP client + translation of LightState into WLED JSON payloads."""

from __future__ import annotations

import time
from typing import Any

import httpx

from spookhub.models import LightState

PRESET_META_KEYS = {"n", "ql", "ib", "sb", "o", "psave", "time", "error", "win"}


class WledError(Exception):
    pass


class WledClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def _req(
        self, method: str, ip: str, path: str, timeout: float, json: Any = None
    ) -> tuple[Any, float]:
        url = f"http://{ip}{path}"
        t0 = time.monotonic()
        try:
            r = await self.http.request(method, url, json=json, timeout=timeout)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise WledError(f"{method} {url}: {type(e).__name__}: {e}") from e
        return data, (time.monotonic() - t0) * 1000.0

    async def info(self, ip: str, timeout: float) -> tuple[dict[str, Any], float]:
        return await self._req("GET", ip, "/json/info", timeout)

    async def state_info(self, ip: str, timeout: float) -> tuple[dict[str, Any], float]:
        """/json/si = state + info in one round trip (cheap health check)."""
        return await self._req("GET", ip, "/json/si", timeout)

    async def set_state(self, ip: str, payload: dict[str, Any], timeout: float) -> float:
        _, ms = await self._req("POST", ip, "/json/state", timeout, json=payload)
        return ms

    async def get_cfg(self, ip: str, timeout: float = 3.0) -> dict[str, Any]:
        return (await self._req("GET", ip, "/json/cfg", timeout))[0]

    async def set_cfg(self, ip: str, body: dict[str, Any], timeout: float = 5.0) -> None:
        await self._req("POST", ip, "/json/cfg", timeout, json=body)

    async def effects(self, ip: str, timeout: float = 3.0) -> list[str]:
        return (await self._req("GET", ip, "/json/effects", timeout))[0]

    async def palettes(self, ip: str, timeout: float = 3.0) -> list[str]:
        return (await self._req("GET", ip, "/json/palettes", timeout))[0]

    async def fxdata(self, ip: str, timeout: float = 3.0) -> list[str]:
        return (await self._req("GET", ip, "/json/fxdata", timeout))[0]

    async def presets(self, ip: str, timeout: float = 3.0) -> dict[str, Any]:
        return (await self._req("GET", ip, "/presets.json", timeout))[0]


def build_payload(
    state: LightState,
    *,
    presets: dict[str, Any],
    seg_ids: list[int],
    master: float = 1.0,
    fallback_bri: int = 128,
    seg_states: dict[int, LightState] | None = None,
) -> dict[str, Any]:
    """Translate a LightState into a /json/state body.

    Presets are expanded from the cached preset file instead of sending {"ps": id}: WLED
    applies "ps" asynchronously, so a brightness in the same request would be overwritten
    and the master-brightness multiplier could not work.

    seg_states: per-segment states merged into the same body's "seg" list, so one request
    sets the whole board and its segments. Only segment fields (fx, pal, sx, ix, col) are
    taken from them; on, bri and transition are board-wide in WLED and come from `state`.
    """
    payload: dict[str, Any] = {}
    preset_seg: list[dict[str, Any]] | None = None
    if state.preset is not None:
        p = presets.get(str(state.preset))
        if p and ("seg" in p or "bri" in p or "on" in p):
            for k, v in p.items():
                if k not in PRESET_META_KEYS and k != "seg":
                    payload[k] = v
            raw = p.get("seg", [])
            preset_seg = [dict(s) for s in (raw if isinstance(raw, list) else [raw])]
        else:
            payload["ps"] = state.preset

    if state.on is not None:
        payload["on"] = state.on
    if state.bri is not None:
        payload["bri"] = state.bri
    if state.transition_ms is not None:
        # "tt" is a one-shot transition (100 ms units); "transition" would persist.
        payload["tt"] = round(state.transition_ms / 100)
        payload.pop("transition", None)

    seg_fields = _seg_fields(state)

    if preset_seg is not None:
        if seg_fields:
            for s in preset_seg:
                s.update(seg_fields)
        payload["seg"] = preset_seg
    elif seg_fields:
        # Address every known segment explicitly; an id-less seg object only hits the
        # main/selected segment depending on WLED version.
        ids = seg_ids or [0]
        payload["seg"] = [{"id": i, **seg_fields} for i in ids]

    if seg_states:
        _merge_seg_states(payload, seg_states, presets)

    if "ps" not in payload and master < 0.999:
        bri = payload.get("bri", fallback_bri)
        payload["bri"] = max(0, min(255, round(bri * master)))
        if payload["bri"] == 0 and payload.get("on", True):
            payload["bri"] = 1
    return payload


def _seg_fields(state: LightState) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if state.fx is not None:
        out["fx"] = state.fx
    if state.pal is not None:
        out["pal"] = state.pal
    if state.sx is not None:
        out["sx"] = state.sx
    if state.ix is not None:
        out["ix"] = state.ix
    if state.colors is not None:
        out["col"] = [list(c) for c in state.colors]
    return out


def _merge_seg_states(
    payload: dict[str, Any], seg_states: dict[int, LightState], presets: dict[str, Any]
) -> None:
    segs: list[dict[str, Any]] = payload.setdefault("seg", [])
    # WLED maps an id-less array entry to the segment at that index; make it explicit so a
    # segment override lands on the right entry.
    for i, s in enumerate(segs):
        s.setdefault("id", i)
    by_id = {s["id"]: s for s in segs}
    for sid in sorted(seg_states):
        st = seg_states[sid]
        fields: dict[str, Any] = {}
        if st.preset is not None:
            # A preset on a segment: that preset's first segment look, on this segment only.
            p = presets.get(str(st.preset))
            raw = p.get("seg") if isinstance(p, dict) else None
            first = raw[0] if isinstance(raw, list) and raw else raw
            if isinstance(first, dict):
                fields = {k: v for k, v in first.items() if k not in ("id", "start", "stop", "n")}
        fields.update(_seg_fields(st))
        if not fields:
            continue
        if sid in by_id:
            by_id[sid].update(fields)
        else:
            by_id[sid] = {"id": sid, **fields}
            segs.append(by_id[sid])
    if not segs:
        del payload["seg"]
