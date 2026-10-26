# Spookhub: Halloween Light & Projection Controller

You are building a production-ready, self-hosted controller that syncs a projector video with several WLED-based LED controllers (ESP32) over WiFi. Read this entire spec before writing code. Start with a short implementation plan, then build in the milestone order at the bottom. **Hard deadline: it must run reliably on October 31.** Milestone 1 must be fully working and deployable before you start Milestone 2. Never leave the project in a broken state between milestones.

---

## 1. Context & hardware

- **Hub machine:** an old x86 mini PC running **Debian 13 minimal (no desktop environment)**, connected over HDMI to a small projector, plus a speaker for audio. It runs everything: backend, web UI, video playback.
- **LED controllers:** 3–4 ESP32 boards (more may be added later) running stock **WLED** (recent 0.15.x or newer), on the same WLAN as the hub.
- **Clients:** the web UI is used from a phone (primary, must be mobile-friendly) and from a desktop browser (used for the timeline editor).
- **Network:** home WLAN. Boards can briefly drop off WiFi, and IPs can change via DHCP. Robustness against this is a core requirement, not an extra.
- **Development:** the code is written on a desktop PC and deployed to the mini PC. Everything must also run on the dev machine without real hardware (see §9, simulators).

## 2. Core concepts (domain model)

- **Board:** a WLED device. Identified permanently by its **MAC address** (from `/json/info` → `mac`), never by IP. It stores the current IP, a user-defined name, last-seen time, connection status, firmware version, LED count, RSSI, and the last known state.
- **Board selection:** a list of board MACs, *or* the special value **"All boards"**. "All boards" is dynamic: it includes boards discovered later, without editing the profile.
- **Light state:** what to send to a board. Supports:
  - a WLED preset ID (selected from the presets read from that board), or
  - an explicit state: on/off, brightness, effect (fx), palette, up to 3 colors, speed, intensity, and transition time, applied to all segments by default.
  - Effects and palettes are offered by name, read from the board's `/json/effects` and `/json/palettes`.
- **Profile:** a saved show configuration. Two types:
  1. **Light profile:** a board selection plus a **base state** for each board (one shared base state, with optional per-board overrides). Activating it applies the base states.
  2. **Video profile:** one video, a board selection, a base state per board (the same as for a light profile), a **cue timeline** (§5), and settings:
     - **Playback mode:** play once, loop, or *idle + trigger* (see below).
     - **Optional idle video:** a separate looped video shown between triggered plays (for example a faint drifting ghost). If none is set, the screen is pure black.
     - **Trigger cooldown:** in seconds.
     - **End behavior:** when the video ends or is stopped, every board returns to the profile's base state. Return to idle video or black.
- **Active profile:** exactly one profile is active at a time. Switching profiles is immediate and safe: any video stops, and the new base states are applied.

## 3. Architecture & tech stack

Use this stack unless there is a strong technical reason not to. If you deviate, explain why in the README.

- **Backend:** Python 3.13 (Debian 13 default), **FastAPI** with uvicorn, fully **asyncio**.
  - `httpx` (async) for WLED HTTP calls.
  - `zeroconf` for mDNS discovery.
  - SQLite via **SQLModel** or SQLAlchemy plus Alembic migrations.
  - Pydantic models throughout.
- **Video player:** **mpv** as a separate managed subprocess, controlled through its **JSON IPC socket**. Write a small async IPC client yourself. Use `observe_property` for `time-pos`, `pause` and `eof-reached`.
  - On Debian minimal there is no X/Wayland. mpv renders directly with `--vo=gpu --gpu-context=drm` and `--hwdec=auto-safe`. Make the vo/context configurable for dev machines.
  - Run mpv with `--idle=yes`, `--keep-open=yes`, `--force-window=yes`, a black background, no OSD, and no on-screen controller.
  - If mpv crashes, the backend restarts it automatically.
- **Frontend:** React + TypeScript + Vite + Tailwind, built to static files and served by FastAPI. Dark theme, fitting the Halloween mood but clean and readable.
- **Realtime:** a single WebSocket from backend to UI. It pushes board status, active profile, playback position and show events. The UI never polls the backend.
- **Media tooling:** `ffmpeg`/`ffprobe` for probing, thumbnails, waveform data, and an optional transcode.
- **Config:** a single TOML file with sane defaults (ports, paths, mpv args, timing constants), overridable by environment variables.
- **Paths:** keep code, config (`/etc/spookhub/`) and data (`/var/lib/spookhub/` for the DB and media) separate.

### Backend modules (suggested)

`boards/` (discovery, registry, health, client, reconciler) · `player/` (mpv process plus IPC) · `show/` (profile engine, cue scheduler, trigger handling) · `media/` (upload, probe, thumbnails, waveform) · `api/` (REST plus WebSocket) · `config`, `db`.

## 4. Board management & robustness (critical)

### 4.1 Discovery

- Browse mDNS `_wled._tcp` continuously.
- Also offer a **"Scan network"** button that probes the hub's local /24 subnet for `GET /json/info` (concurrency-limited, short timeouts) for networks where mDNS is unreliable.
- Allow adding a board manually by IP.
- Newly found boards appear as **"Discovered"**. The user clicks **"Adopt"** to add them, giving them a name. Only adopted boards are used in shows. "All boards" means all *adopted* boards.

### 4.2 Desired-state reconciliation (the key reliability pattern)

- The backend keeps a **desired state** per board, meaning what it *should* currently show according to the active profile, the running cue, or a manual override.
- Every command updates the desired state first, then attempts delivery.
- **Health check:** a lightweight `GET /json/info` or `/json/state` every ~3 s, or the WLED WebSocket (`/ws`) for push updates with polling as fallback.
- **Status levels:** `online`, `degraded` (latency or missed checks), `offline` (several consecutive failures).
- **When a board comes back online, or is found at a new IP via mDNS or scan matched by MAC, the backend automatically re-applies its current desired state.** The UI shows a short "reconnected, state restored" event.
- **Retries:**
  - **Time-critical cue commands:** one attempt with a short timeout (~300 ms) plus one quick retry, never blocking other boards. If both fail, reconciliation fixes the board on reconnect.
  - **Non-time-critical commands** (such as profile activation): exponential backoff.
- **Per-board command queue with "latest wins":** if a newer command is queued while an older one is still pending, drop the older one. One slow board must never delay the others. Send to all boards concurrently.
- Track the round-trip latency per board, and show it in the UI.

### 4.3 Boards page (UI)

- A live list or card grid with:
  - name, IP, MAC, online status (colored), RSSI as signal bars, last seen, firmware, LED count, latency
  - the board's current effect, brightness and on/off state.
- **Per-board actions:**
  - rename
  - identify (blink the board white for 2 s, then restore)
  - quick on/off and brightness
  - open the native WLED UI in a new tab
  - refresh presets, effects and palettes
  - remove
- **"Recommended settings" helper:** checks each board and offers a one-click fix through WLED's `/json/cfg` API. Confirm before writing. At minimum, check that **WiFi sleep is disabled**. The UI also shows a hint that a DHCP reservation or static IP is recommended.
- **New-board help:** a short static "add a new board" guide in the UI. It links to the official WLED web installer, explains the `WLED-AP` setup access point, and says the board appears here automatically once it joins the WLAN. Do not try to automate flashing.

## 5. Video timeline editor (desktop-first; usable on tablet)

### Upload

- Upload a video with a progress bar and validation by file type and size.
- Probe it with ffprobe and store the duration, resolution and codec.
- Generate a thumbnail strip and **audio waveform** data.
- If the codec is not H.264/HEVC, or the resolution is above 1080p, offer a transcode to H.264 1080p (yuv420p, AAC). This keeps hardware decoding reliable on the mini PC.

### Timeline UI

- **Browser preview player:** the same file is served with HTTP range support. It has play/pause and a scrubbable playhead. **Click or drag anywhere on the timeline to jump.** Keyboard shortcuts: space, arrow keys for frame or ±0.1 s steps, `C` to add a cue at the playhead.
- **Layout:**
  - a waveform plus a thumbnail strip along the top
  - **one lane per board in the profile's selection**
  - one extra **"All selected boards"** lane at the top.
- **Cues are blocks on lanes.** Each cue has:
  - a start time
  - a target: all selected boards, or one or more specific boards
  - a light state (preset or explicit state, §2)
  - an optional **duration**. When the duration ends, the targeted boards return to the profile's base state, unless another cue takes over. With no duration, the state holds until the next cue or the video ends.
  - an optional transition time.
- **Editing:** drag to move, drag the edges to resize, a side panel to edit details, duplicate, delete, multi-select, and snap to other cues or the playhead. Undo/redo at least for move/add/delete. Zoom the timeline horizontally.
- **Resolving overlaps:**
  - On a given board at a given time, the cue that started most recently wins.
  - "All selected boards" cues and board-specific cues follow the same rule.
  - Show a visual warning when cues overlap on a board.
- **Convenience:**
  - **"Capture from board":** create a cue from a board's current live state, so you can tweak effects in the WLED UI and then grab them.
  - **Cue templates:** Flicker, Strobe, Blackout, Blood red, Lightning flash. Make it easy to add more.
- **Live audition:** a toggle where browser preview playback fires the cues on the real boards in sync, so you can tune the effect from the desk.
- **"Play on projector from here":** starts the real show on the hub's mpv at the current playhead position.
- **Autosave** with a "saved" indicator. Profiles can be **exported and imported as JSON**, including cues but excluding the video file.

## 6. Show engine & sync

- **The cue scheduler runs in the backend and is driven by mpv's actual `time-pos`**, not by a free-running timer.
  - Between position updates, interpolate with a monotonic clock for sub-frame precision.
  - Fire a cue when the playhead crosses its start time.
- **Seek and pause:** on a seek, recompute what should be active at the new position and apply that state to every board. Don't replay every cue in between. While paused, hold the current states.
- **Latency compensation:** fire each board's commands early by that board's measured average latency (capped, configurable). Add a global offset setting in ms to fine-tune perceived sync.
- **Show sequence:**
  1. Activating a video profile applies the base states, then starts the idle video (or black) or starts the main video, depending on the playback mode.
  2. At the end or on stop, all selected boards return to their base states, and the idle video or black screen comes back.
- **Triggers** start the main video in a profile in *idle + trigger* mode, respecting the cooldown:
  - a **big "TRIGGER" button** in the UI
  - `POST /api/trigger` for external devices, such as a PIR motion sensor on a spare ESP32.
  - Document an example: an ESPHome or Arduino sketch that calls this endpoint on motion.
- **Panic / blackout:** a global, always-visible button. It stops the video, blacks out the projector and switches off all boards, and stays in this state until it is released.
- **Master brightness** for all boards, applied as a multiplier on top of profile states.

## 7. Web UI pages

1. **Dashboard (phone-first):**
   - the active profile and playback state with a progress bar
   - big buttons: Trigger, Play/Stop, Next profile, Blackout
   - a master brightness slider
   - a compact board health strip
   - a live event log (last ~20 events).
2. **Profiles:** list, create, edit, duplicate, delete, activate.
   - Choose the type (light or video), the board selection (multi-select checkboxes plus an **"All boards"** toggle), base states and playback settings.
   - Video profiles link to the timeline editor.
3. **Timeline editor:** §5.
4. **Boards:** §4.3.
5. **Media library:** uploaded videos with thumbnail, duration, codec, transcode status, the profiles using each one, and delete (blocked if a profile uses it).
6. **Settings:** global sync offset, health interval, default transitions, optional UI PIN, mpv output settings, and a **"Test pattern"** button that shows a projector alignment grid for keystone setup.

Mobile: the dashboard, profiles and boards pages must be fully usable on a 375 px wide phone. The timeline must at least open on mobile, but it may be read-only there.

## 8. API, security, operations

- REST API under `/api/…`, WebSocket at `/ws`, and automatic OpenAPI docs.
- **Security:** LAN-only use. An optional PIN set in settings protects all mutating endpoints and the UI with a session cookie. `/api/trigger` has its own optional token. No auth by default, so setup stays easy.
- **Logging:** structured logs to journald, with a log level setting.
- **systemd:**
  - one `spookhub.service`, which manages mpv as a child process, or a separate `spookhub-player.service` if that is cleaner
  - `Restart=always`, starts at boot, waits for network-online.
  - Running mpv on DRM needs access to the video and render devices. Set up a dedicated user that is in the `video`, `render` and `audio` groups.
- **Installer:** `scripts/install.sh`, idempotent, for a fresh Debian 13 minimal install. It:
  - installs apt packages: `mpv`, `ffmpeg`, `python3-venv`, `avahi-daemon` and audio packages (ALSA utilities or PipeWire), plus `intel-media-va-driver` or `i965-va-driver` for Intel hardware decoding
  - creates the user, directories and venv
  - builds or copies the frontend
  - installs the systemd units
  - **disables console blanking and sleep/suspend** (`consoleblank=0`, mask the sleep targets)
  - prints the URL at the end.
- **Deploy script:** `scripts/deploy.sh` to rsync from the dev machine to the mini PC over SSH and restart the service.
- **Backups:** a download or restore endpoint for the SQLite DB plus config (without media).

## 9. Quality requirements

- **WLED simulator:** a small FastAPI app that mimics the WLED endpoints used: `/json/info`, `/json/state`, `/json/effects`, `/json/palettes`, `/json/presets`, `/json/cfg`.
  - It can run N fake boards on different ports.
  - It supports **simulated dropouts and latency**.
  - It logs received commands with timestamps.
- **mpv dev mode:** run the full system on a normal desktop using mpv in a window, or a null video output for tests.
- **Tests (pytest):**
  - cue resolution logic (overlaps, durations, return to base, seek recomputation)
  - reconciliation after reconnect and IP change matched by MAC
  - the latest-wins queue
  - API endpoints
  - one end-to-end test with the simulator plus mpv null output, checking that cues reach the fake boards within ±100 ms of their timestamps.
  - Frontend: type-check and lint cleanly.
- **Code quality:** typed, ruff-formatted, with clear module boundaries. Comments explain *why*, not *what*.
- **README:** architecture overview, dev setup, deploy steps, hardware tips (black video backgrounds, scrim/window projection, static IPs), and troubleshooting.

## 10. Milestones (build in this order; each must end deployable and tested)

1. **M1 (core show, P0):**
   - board discovery, adoption, health and reconciliation
   - light profiles
   - mpv control
   - video profiles with cues edited as a simple **list form** (time, target, state, duration)
   - the cue scheduler with seek handling
   - dashboard with trigger and blackout
   - installer and systemd
   - simulator and core tests.
2. **M2 (timeline editor, P1):** the full visual timeline from §5, with waveform, thumbnails, drag editing, live audition and capture from board.
3. **M3 (polish, P2):**
   - recommended-settings helper
   - transcoding
   - export/import
   - PIN
   - test pattern
   - backups
   - cue templates
   - the external trigger example sketch.

## Working rules

- Begin by presenting a concise plan: directory layout, data model, key decisions and risks. Then implement M1.
- Write the simulator early, and develop against it.
- After each milestone, run all tests, give a short summary of what works and any known gaps, and ask before continuing.
- Don't add features beyond this spec without asking. If something in the spec is ambiguous or technically problematic, choose the more robust option and note it.
