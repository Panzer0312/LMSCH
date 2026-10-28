# Development

Requires Python 3.13 via [uv](https://docs.astral.sh/uv/), Node 20+, mpv, ffmpeg.

```bash
cd backend && uv sync          # pins 3.13 via .python-version
cd frontend && npm install
scripts/dev.sh                 # 3 simulated boards + backend + windowed mpv + Vite :5173
scripts/dev.sh --no-mpv        # without video player
```

UI: Boards → add `127.0.0.1:9001`, `:9002`, `:9003` → adopt.
mpv on desktop: `SPOOKHUB_MPV__GPU_CONTEXT=x11egl` or `wayland`; tests use `vo=null`.

## WLED simulator (`simulator/wled_sim.py`)

Mimics `/json/info|si|state|effects|palettes|presets|cfg`.

```bash
cd backend && uv run python ../simulator/wled_sim.py --boards 4 --base-port 9001
curl -X POST 'localhost:9001/sim/latency?ms=120'    # slow WiFi
curl -X POST 'localhost:9001/sim/dropout?on=true'   # hang, then 503
curl localhost:9001/sim/log                         # received commands + timestamps
```

## Checks

```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
cd frontend && npx tsc -b && npm run lint
```

| Test file | Covers |
|---|---|
| `test_resolve.py` | Cue resolution: overlaps, durations, base return, seek |
| `test_queue.py` | Latest-wins queue |
| `test_reconcile.py` | Reconnect + IP change by MAC |
| `test_api.py`, `test_timeline_api.py`, `test_polish.py` | REST endpoints, M2/M3 features |
| `test_e2e.py` | Real mpv (null out) + simulator; every cue within ±100 ms, prints error |
