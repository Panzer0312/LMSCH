"""Fake WLED boards over HTTP for local development and tests."""

import argparse
import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

EFFECTS: list[str] = [
    "Solid",
    "Blink",
    "Breathe",
    "Wipe",
    "Wipe Random",
    "Random Colors",
    "Sweep",
    "Dynamic",
    "Colorloop",
    "Rainbow",
    "Scan",
    "Scan Dual",
    "Fade",
    "Theater",
    "Running",
    "Saw",
    "Twinkle",
    "Fire 2012",
    "Lightning",
    "Strobe",
]

# WLED fxdata strings: "<sliders>;<colors>;<palette>" per effect, same index as EFFECTS.
FXDATA: list[str] = ["" if i == 0 else "@Speed,Intensity;!,!;!" for i in range(len(EFFECTS))]

PALETTES: list[str] = [
    "Default",
    "* Random Cycle",
    "* Color 1",
    "Party",
    "Cloud",
    "Lava",
    "Ocean",
    "Forest",
    "Rainbow",
    "Fire",
]

DROPOUT_DELAY_S = 10.0


def _default_state() -> dict[str, Any]:
    return {
        "on": True,
        "bri": 128,
        "transition": 7,
        "ps": -1,
        "seg": [
            {
                "id": 0,
                "on": True,
                "bri": 255,
                "fx": 0,
                "pal": 0,
                "sx": 128,
                "ix": 128,
                "col": [[255, 160, 0], [0, 0, 0], [0, 0, 0]],
            }
        ],
    }


def _default_presets() -> dict[str, Any]:
    return {
        "0": {},
        "1": {
            "n": "Spooky",
            "on": True,
            "bri": 200,
            "seg": [{"fx": 2, "pal": 5, "col": [[255, 0, 0], [0, 0, 0], [0, 0, 0]]}],
        },
        "2": {
            "n": "Calm",
            "on": True,
            "bri": 60,
            "seg": [{"fx": 0, "col": [[80, 0, 160], [0, 0, 0], [0, 0, 0]]}],
        },
    }


@dataclass
class FakeBoard:
    index: int
    port: int
    mac: str = ""
    name: str = ""
    led_count: int = 60
    rssi: int = -55
    fw: str = "0.15.0"
    state: dict[str, Any] = field(default_factory=_default_state)
    presets: dict[str, Any] = field(default_factory=_default_presets)
    cfg: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0
    dropout: bool = False
    log: list[dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if not self.mac:
            self.mac = f"aabbccdd00{self.index:02x}"
        if not self.name:
            self.name = f"Sim-{self.index}"
        if not self.cfg:
            self.cfg = {"wifi": {"sleep": True}, "id": {"name": self.name}}

    def info(self) -> dict[str, Any]:
        return {
            "ver": self.fw,
            "vid": 2405180,
            "leds": {"count": self.led_count},
            "name": self.name,
            "mac": self.mac,
            "ip": "127.0.0.1",
            "wifi": {"rssi": self.rssi, "signal": 80},
            "arch": "esp32",
            "uptime": int(time.monotonic() - self.started),
        }


def _apply_value(target: dict[str, Any], key: str, value: Any) -> None:
    if key == "on" and value == "t":
        target["on"] = not target.get("on", False)
    else:
        target[key] = copy.deepcopy(value)


def _apply_seg(state: dict[str, Any], seg: Any) -> None:
    items = [seg] if isinstance(seg, dict) else seg
    if not isinstance(items, list):
        return
    segments: list[dict[str, Any]] = state.setdefault("seg", [])
    for item in items:
        if not isinstance(item, dict):
            continue
        if "id" in item:
            targets = [s for s in segments if s.get("id") == item["id"]]
        else:
            targets = segments
        for target in targets:
            for key, value in item.items():
                if key != "id":
                    _apply_value(target, key, value)


def _save_preset(board: FakeBoard, body: dict[str, Any]) -> None:
    """psave: store the current state (post-apply) as a preset, like WLED does."""
    pid = str(body["psave"])
    preset: dict[str, Any] = {"n": body.get("n", f"Preset {pid}")}
    for flag in ("ib", "sb"):
        if flag in body:
            preset[flag] = body[flag]
    if body.get("sb", True):
        preset["on"] = board.state.get("on", True)
        preset["bri"] = board.state.get("bri", 128)
        preset["seg"] = copy.deepcopy(board.state.get("seg", []))
    board.presets[pid] = preset


def apply_state(board: FakeBoard, body: dict[str, Any]) -> None:
    state = board.state
    if "ps" in body:
        preset = board.presets.get(str(body["ps"]))
        if preset:
            for key in ("on", "bri"):
                if key in preset:
                    _apply_value(state, key, preset[key])
            if "seg" in preset:
                _apply_seg(state, preset["seg"])
            state["ps"] = body["ps"]
    for key in ("on", "bri", "transition"):
        if key in body:
            _apply_value(state, key, body[key])
    if "seg" in body:
        _apply_seg(state, body["seg"])
    if "psave" in body:
        _save_preset(board, body)


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = copy.deepcopy(value)


def make_app(board: FakeBoard) -> FastAPI:
    app = FastAPI(title=f"WLED sim {board.name}")

    @app.middleware("http")
    async def simulate_network(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # /sim stays reachable so tests can switch dropout off again.
        if not request.url.path.startswith("/sim"):
            if board.dropout:
                # Real dropouts hang rather than refuse; clients must time out.
                await asyncio.sleep(DROPOUT_DELAY_S)
                return JSONResponse({"error": "dropout"}, status_code=503)
            if board.latency_ms > 0:
                await asyncio.sleep(board.latency_ms / 1000)
        return await call_next(request)

    @app.get("/json/info")
    async def get_info() -> dict[str, Any]:
        return board.info()

    @app.get("/json/state")
    async def get_state() -> dict[str, Any]:
        return board.state

    @app.post("/json/state")
    @app.post("/json/si")
    async def post_state(request: Request) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
        board.log.append({"t": time.time(), "mono": time.monotonic(), "body": body})
        apply_state(board, body)
        return board.state

    @app.get("/json")
    async def get_all() -> dict[str, Any]:
        return {
            "state": board.state,
            "info": board.info(),
            "effects": EFFECTS,
            "palettes": PALETTES,
        }

    @app.get("/json/si")
    async def get_si() -> dict[str, Any]:
        return {"state": board.state, "info": board.info()}

    @app.get("/json/effects")
    async def get_effects() -> list[str]:
        return EFFECTS

    @app.get("/json/fxdata")
    async def get_fxdata() -> list[str]:
        return FXDATA

    @app.get("/json/palettes")
    async def get_palettes() -> list[str]:
        return PALETTES

    @app.get("/presets.json")
    async def get_presets() -> dict[str, Any]:
        return board.presets

    @app.get("/json/cfg")
    async def get_cfg() -> dict[str, Any]:
        return board.cfg

    @app.post("/json/cfg")
    async def post_cfg(request: Request) -> dict[str, Any]:
        body = await request.json()
        if isinstance(body, dict):
            _deep_merge(board.cfg, body)
        return board.cfg

    @app.post("/sim/latency")
    async def sim_latency(ms: float) -> dict[str, Any]:
        board.latency_ms = ms
        return {"latency_ms": board.latency_ms}

    @app.post("/sim/dropout")
    async def sim_dropout(on: bool) -> dict[str, Any]:
        board.dropout = on
        return {"dropout": board.dropout}

    @app.get("/sim/log")
    async def get_log() -> list[dict[str, Any]]:
        return board.log

    @app.delete("/sim/log")
    async def clear_log() -> dict[str, Any]:
        board.log.clear()
        return {"cleared": True}

    return app


async def serve(boards: list[FakeBoard], host: str = "127.0.0.1") -> None:
    configs = [
        uvicorn.Config(make_app(b), host=host, port=b.port, log_level="warning") for b in boards
    ]
    servers = [uvicorn.Server(c) for c in configs]
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fake WLED boards.")
    parser.add_argument("--boards", type=int, default=3)
    parser.add_argument("--base-port", type=int, default=9001)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--latency-ms", type=float, default=0)
    args = parser.parse_args()

    boards = [
        FakeBoard(index=i, port=args.base_port + i, latency_ms=args.latency_ms)
        for i in range(args.boards)
    ]
    for b in boards:
        print(f"{b.name}: http://{args.host}:{b.port}  mac={b.mac}")
    asyncio.run(serve(boards, host=args.host))


if __name__ == "__main__":
    main()
