# Spookhub

Self-hosted Halloween controller. Plays video on a projector (mpv, DRM/KMS) and keeps several WLED
LED boards (ESP32) in sync over WiFi. Runs on one Debian 13 mini PC, controlled from phone or
desktop browser.

**Status:** M1 core show, M2 timeline editor, M3 polish and phase 1 (setup page, preflight, sync
calibration, show mode, run log, watchdog and fallbacks) and phase 2 (timeline editing: markers,
onsets, templates, time editing, clipboard, effect picker, WLED segments) and phase 3 (video setup
page, display profiles, keystone/mask shader, alignment patterns) and phase 4 (scenes, sound cues,
soundboard, ambient layer, idle track, idle → main fade, moments page) built and tested against the
simulator.
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

## Debian 13 hub: required packages

Single source of truth. `install.sh` installs every row marked yes. Install "no" rows by hand when
they apply.

| Package | Why | Installed by `install.sh` |
|---|---|---|
| `mpv` | Video playback on DRM/KMS. Needs ≥ 0.38 for `video-scale-x/y` and `glsl-shader-opts` (warp). Dev tested 0.41; verify `mpv --version` on the hub. | yes |
| `ffmpeg` | Media probe, waveform, onset analysis, test patterns | yes |
| `python3` | Backend runtime (3.13) | yes |
| `python3-venv` | Virtualenv in `/opt/spookhub/venv` | yes |
| `python3-pip` | Installs the backend into the venv | yes |
| `avahi-daemon` | Announces `<hostname>.local` | yes |
| `libnss-mdns` | Resolves `.local` names (WLED boards) | yes |
| `alsa-utils` | Audio test, `aplay -l` device list, dmix HDMI autodetect | yes |
| `intel-media-va-driver` | VA-API hardware decode, Intel Broadwell+ (falls back to `-non-free`) | yes (Intel CPU only) |
| `i965-va-driver` | VA-API hardware decode, older Intel | yes (Intel CPU only) |
| `mesa-va-drivers` | VA-API hardware decode, AMD | yes |
| `vainfo` | Check VA-API works (`vainfo`) | yes |
| `libgl1-mesa-dri` | Mesa GPU driver for mpv `vo=gpu` and the warp shader | yes |
| `libegl1` | EGL for mpv on DRM | yes |
| `mesa-vulkan-drivers` | Only if `vo=gpu-next` | no |
| `fonts-dejavu-core` | Font for `drawtext` in alignment patterns | yes |
| `nodejs`, `npm` | Frontend build on the hub | yes |
| `git` | `git pull` deploy, version stamp | yes |
| `ca-certificates`, `curl` | HTTPS for npm and pip | yes |
| `rsync` | Copies the checkout to `/opt/spookhub` | yes |

## Debian desktop for development

Hub packages above, plus:

| Package | Why |
|---|---|
| `uv` | Backend env, tests, ruff (`pipx install uv` or the Astral installer) |
| `mpv` windowed context | `scripts/dev.sh` runs mpv in a window: `gpu-context=x11egl` on X11, `wayland` on Wayland |

## Docs

| File | Content |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Modules, data model, reliability design, cue rules |
| [docs/api.md](docs/api.md) | REST routes and WebSocket messages |
| [docs/features.md](docs/features.md) | What each UI page and feature does |
| [docs/timeline.md](docs/timeline.md) | Timeline editor: lanes, tracks, keys, sound cues |
| [docs/decisions.md](docs/decisions.md) | Choices made where the spec was open |
| [docs/development.md](docs/development.md) | Dev setup, simulator, tests and checks |
| [docs/operations.md](docs/operations.md) | Deploy, paths, hardware tips, troubleshooting |
| [docs/roadmap/](docs/roadmap/README.md) | Five-phase plan for the next work |
| [docs/spec-v1.md](docs/spec-v1.md) | Frozen original spec (M1–M3) |
| [AGENTS.md](AGENTS.md) | Rules for coding agents, including keeping these docs current |
