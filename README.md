# Spookhub

Self-hosted Halloween controller. Plays video on a projector (mpv, DRM/KMS) and keeps several WLED
LED boards (ESP32) in sync over WiFi. Runs on one Debian 13 mini PC, controlled from phone or
desktop browser.

**Status:** M1 core show, M2 timeline editor, M3 polish and phase 1 (setup page, preflight, sync
calibration, show mode, run log, watchdog and fallbacks) built and tested against the simulator.
Not yet run on real hardware. Next work: [`docs/roadmap/`](docs/roadmap/README.md).

## Quick start (dev)

```bash
cd backend && uv sync && cd ../frontend && npm install && cd ..
scripts/dev.sh            # 3 simulated boards + backend + windowed mpv + Vite on :5173
```

Boards page → add `127.0.0.1:9001`, `:9002`, `:9003` by IP → adopt.

## Deploy

Current workflow: pull the repo on the hub and run the installer there.

```bash
cd ~/spookhub && git pull && sudo scripts/install.sh
```

Alternative from the dev machine: `scripts/deploy.sh user@minipc [--install]`.
Details and known issues: [docs/operations.md](docs/operations.md).

## Docs

| File | Content |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Modules, data model, reliability design, cue rules, API and WebSocket |
| [docs/features.md](docs/features.md) | What each UI page and feature does |
| [docs/decisions.md](docs/decisions.md) | Choices made where the spec was open |
| [docs/development.md](docs/development.md) | Dev setup, simulator, tests and checks |
| [docs/operations.md](docs/operations.md) | Deploy, paths, hardware tips, troubleshooting |
| [docs/roadmap/](docs/roadmap/README.md) | Five-phase plan for the next work |
| [docs/spec-v1.md](docs/spec-v1.md) | Frozen original spec (M1–M3) |
| [AGENTS.md](AGENTS.md) | Rules for coding agents, including keeping these docs current |
