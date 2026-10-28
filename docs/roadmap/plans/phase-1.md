# Phase 1 implementation plan: show-night reliability and device setup

Spec: `docs/roadmap/phase-1-show-night.md` (binding). Project rules: `AGENTS.md` (binding).
Branch: `roadmap/phase-1`. Baseline: 34 backend tests green, ruff/tsc/oxlint clean.

## Global constraints

- Python 3.13, FastAPI, asyncio, SQLModel. Schema changes need a new Alembic revision in
  `backend/spookhub/migrations/versions/` (down_revision = current head). Check with `uv run alembic -c alembic.ini heads` from `backend/`.
- New runtime settings go into `RuntimeSettings` in `backend/spookhub/models.py` with defaults that keep today's behavior.
- Board commands only through `BoardManager` (desired state + queue). mpv only through `MpvPlayer`.
- UI never polls the backend for live state: publish over `hub.bus.publish(type, data)`; frontend reducer `frontend/src/live.ts`.
- Setup/test actions (test sound, calibration, test-all) refuse with HTTP 409 while a show plays
  (engine snapshot `playing` true / main video loaded), and restore the idle state afterwards.
- No new Python or npm dependencies in phase 1.
- Tests: pytest in `backend/tests/`, use the existing fixtures in `conftest.py` and the WLED simulator. Every task adds tests for its backend logic.
- Checks before commit: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .` and, if frontend touched, `cd frontend && npx tsc -b && npm run lint`.
- Commit messages: conventional commits, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not edit docs except where the task says; Task 12 updates docs.

### Task 1: Settings, audio controls, caps, version

Files: `backend/spookhub/models.py`, `backend/spookhub/player/process.py`, `backend/spookhub/hub.py`, `backend/spookhub/api/routes_show.py`, tests.

- `RuntimeSettings` new fields: `mpv_drm_connector: str | None = None`, `mpv_drm_mode: str | None = None`,
  `mpv_audio_device: str | None = None`, `master_volume: float = Field(100, ge=0, le=130)`,
  `volume_cap: float = Field(100, ge=0, le=130)`, `brightness_cap: float = Field(1.0, ge=0, le=1)`,
  `audio_delay_s: float = Field(0.0, ge=-5, le=5)`, `show_lock: bool = False`.
- `MpvPlayer`: overrides `drm_connector_override`, `drm_mode_override`, `audio_device_override`
  (like `vo_override`), emitted as `--drm-connector=`, `--drm-mode=`, `--audio-device=` in `args()`
  (only when set; config `extra_args` still appended last). New `async apply_audio(volume: float, delay_s: float)`
  sets mpv `volume` and `audio-delay`; new `async set_audio_device(name)` sets property `audio-device` live.
  The player remembers the last applied volume/delay and re-applies them after every (re)start and after every `load()`.
- `Hub`: on start apply overrides from settings. `_on_settings`: connector/mode change ⇒ `player.restart()`;
  audio device change ⇒ live `set_audio_device`; volume, cap or delay change ⇒ `apply_audio(min(master_volume, volume_cap), audio_delay_s)`.
  Board master brightness lambda becomes `min(master_brightness, brightness_cap)`.
- `PUT /api/settings`: clamp `master_volume` to `volume_cap` and `master_brightness` to `brightness_cap` before saving. `show_lock` is not settable here (Task 5 owns it): pop it like `active_profile_id`.
- `GET /api/state` and the WS snapshot gain `"version": {"commit": ..., "installed_at": ...}` read once from
  `version.json` at the project root (`Path(spookhub.__file__).resolve().parents[2] / "version.json"`), or `{"commit": "dev"}` if missing.
- Tests: args contain overrides; settings change triggers the right player calls (fake player); caps clamp; version fallback.

### Task 2: Device discovery, test sound, trigger tracking, hubcheck

Files: new `backend/spookhub/player/devices.py`, new `backend/spookhub/show/sounds.py`, new `backend/spookhub/api/routes_setup.py`, `backend/spookhub/main.py`, `backend/spookhub/show/engine.py` (trigger tracking only), new `scripts/hubcheck.sh`, tests.

- `devices.py` (pure, `root: Path = Path("/sys/class/drm")` parameter for tests): `drm_outputs(root) -> list[{name, status, modes, enabled}]`
  from `card*-*/status`, `modes`, `enabled`; `name` is the connector without the `cardN-` prefix (e.g. `HDMI-A-1`), connected first.
  `parse_audio_devices(raw: list[dict]) -> list[{name, description}]` from mpv `audio-device-list`.
- `MpvPlayer.audio_devices()` reads `audio-device-list` via IPC (empty list if mpv down).
- `sounds.py`: `ensure_test_sound(data_dir) -> Path`, ffmpeg-generated once (~2 s: rising sine sweep 200→800 Hz then a short low "boo" tone, AAC/ogg or wav), cached like `show/testpattern.py`.
- `routes_setup.py` (prefix `/api/setup`): `GET outputs`, `GET audio_devices`, `POST test_sound` (409 while a show plays;
  plays the sound once through mpv, then returns to the previous idle state the same way `set_test_pattern` does), `GET triggers`.
- Engine: every `trigger(source)` call (accepted or not) records `last_trigger = {ts, source, accepted, message}` and publishes WS `trigger_seen` with it; included in `snapshot()`.
- `scripts/hubcheck.sh`: prints mpv version, `vainfo` summary (or "missing"), DRM connectors + status, `aplay -l`, free disk on `/var/lib/spookhub` (or `/`), `timedatectl` NTP sync, spookhub service status, `/opt/spookhub/version.json`. Plain bash, no root needed, never fails hard.
- Tests: `drm_outputs` on a fake sysfs tree in tmp_path; audio parse; `GET /api/setup/triggers` reflects a trigger; test_sound 409 while playing.

### Task 3: Preflight checks

Files: new `backend/spookhub/show/preflight.py`, `backend/spookhub/player/process.py` (restart timestamps), `backend/spookhub/hub.py`, `backend/spookhub/api/routes_setup.py`, tests.

- `Check` model `{id, label, status: "ok"|"warn"|"fail", detail, fix: str | None}` (fix = UI route hint such as `/boards` or `/setup`).
- Checks (in this order): `boards_online` (fail if any adopted board offline, warn if degraded or none adopted),
  `latency` (warn if any adopted online board RTT ≥ 100 ms), `wifi_sleep` (warn if a cached recommendation says WiFi sleep is on; unknown ⇒ ok with detail "not checked"),
  `mpv` (fail if mpv enabled and not running), `audio_device` (warn if `mpv_audio_device` set but not in the device list),
  `media` (fail if active video profile's media/idle file missing; warn if codec not h264/hevc or height > 1080),
  `disk` (warn < 2 GB free in data dir), `clock` (warn unless `timedatectl show -p NTPSynchronized --value` == yes; skip with ok "unknown" if timedatectl missing),
  `mpv_restarts` (warn if ≥ 1 restart in the last hour, fail if ≥ 3), `trigger_recent` (only for idle_trigger profiles: warn if no trigger seen in 30 min).
- `Preflight` service on the hub: runs all checks every 10 s in a background task, publishes WS `preflight` `{checks, worst}` only when the result changed; `GET /api/preflight` runs immediately and returns the same.
- `POST /api/preflight/test_all`: 409 while a show plays; all adopted boards white full brightness for 2 s via identify-like override, test sound, test pattern 2 s, then restore idle state.
- Tests: each check's ok/warn/fail branch with fakes; publish-on-change only.

### Task 4: Sync calibration and per-board offset

Files: `backend/spookhub/models.py` (Board.offset_ms), new migration, `backend/spookhub/boards/registry.py`, `backend/spookhub/api/routes_boards.py` (PATCH accepts offset_ms), new `backend/spookhub/show/calibration.py`, `backend/spookhub/show/engine.py`, `backend/spookhub/api/routes_setup.py`, tests.

- `Board.offset_ms: float = 0` (−500..500), persisted, in board snapshot, settable via existing `PATCH /api/boards/{mac}`.
- `latency_comp_ms(mac)` adds the board's `offset_ms` (positive = fire earlier). Keep the existing cap on the measured part only.
- `calibration.py`: `ensure_calibration_clip(data_dir) -> Path`: 20 s 1920x1080 black clip, white full-frame flash of 100 ms plus 1 kHz beep of 100 ms at every even second (0, 2, …, 18), H.264 + AAC, cached. `calibration_cues() -> list[cue]` matching the flashes (white, bri 255, duration 0.1 s, all boards).
- Engine `set_calibration(on: bool)`: 409/RuntimeError while a show plays; on: loads clip looped and runs the normal scheduler over a synthetic in-memory profile (all adopted boards, base = off) so global and per-board offsets apply exactly as in a show; off: back to the active profile idle state. Snapshot gains `calibrating`.
- `POST /api/setup/calibration {on}`.
- Tests: per-board offset changes fire time in the scheduler (simulator log timestamps or unit test of latency comp); calibration on/off state transitions; clip cue times.

### Task 5: Show lock and persistent run log

Files: `backend/spookhub/auth.py` or new `backend/spookhub/api/lock.py`, `backend/spookhub/main.py`, `backend/spookhub/models.py` (EventLog), new migration, `backend/spookhub/events.py`, new `backend/spookhub/api/routes_events.py`, `backend/spookhub/api/routes_show.py`, tests.

- Lock middleware: when `settings.show_lock` is true, any non-GET request returns 423 `{"detail": "Show mode is on"}` unless the path is allowed:
  `/api/trigger`, `/api/show/*`, `/api/blackout`, `/api/auth/*`, `/api/show_lock`, `/api/preflight/*`, `/api/boards/{mac}/state` (quick manual override), `/api/boards/{mac}/identify`,
  and `PUT /api/settings` only when the body changes nothing but `master_brightness`, `master_volume`.
- `POST /api/show_lock {on: bool, pin?: str}`: turning on always allowed; turning off requires a correct PIN when a PIN is set (401 otherwise). Publishes `settings`.
- `EventLog` table (id, ts float, level, kind, message, data JSON), index on ts. `EventBus.event()` gains optional `kind` (default "info"); trigger events use kind "trigger", board reconnects "board", mpv restarts "player".
  A writer task flushes buffered entries to the DB every 1 s (never blocks the publish path). Retention: delete rows older than 7 days at startup and every 6 h.
- `GET /api/events?since=<ts>&kind=<k>&limit=<n≤1000>` newest first; `GET /api/events/stats?since=<ts>` ⇒ `{triggers, by_hour: [{hour_ts, count}]}` (accepted triggers only).
- Tests: lock blocks a cue edit with 423 and allows trigger/blackout; unlock with/without PIN; events persisted after flush and survive a new bus; stats counting; retention.

### Task 6: Stall watchdog, systemd watchdog, board fallback

Files: `backend/spookhub/show/engine.py`, new `backend/spookhub/sdnotify.py`, `backend/spookhub/hub.py`, `deploy/spookhub.service`, `backend/spookhub/boards/client.py`, `backend/spookhub/boards/registry.py`, `backend/spookhub/api/routes_boards.py`, `simulator/wled_sim.py`, tests.

- Stall detection in the engine: while the main video is playing (not paused, not loading), if mpv `time-pos` has not advanced for 2 s ⇒ event (kind "player") "video stalled, restarting", `player.restart()`, then resume the same file at the last good position once; a second stall in the same play ⇒ end the show (`_end_show`) and go idle.
- `sdnotify.py`: minimal `notify(msg)` over the `NOTIFY_SOCKET` unix datagram socket (abstract `@` names supported), no-op when unset. Hub sends `READY=1` after start, and `WATCHDOG=1` every `WATCHDOG_USEC/2` from an asyncio task (so a blocked event loop stops the pings). Unit: `Type=notify`, `WatchdogSec=30`, `NotifyAccess=main`.
- Board fallback: `POST /api/boards/fallback` (all adopted boards in the active profile's selection) and `POST /api/boards/{mac}/fallback`: send the board's current resolved base state, save it as WLED preset 250 named "Spookhub fallback" (`{"psave": 250, "n": "Spookhub fallback", "ib": true, "sb": true}` after applying state), then set the boot preset via `/json/cfg` `{"def": {"ps": 250, "on": true}}`. Report per board ok/error. Simulator supports `psave` and stores `cfg.def`.
- Tests: stall ⇒ restart + resume (fake player clock); second stall ⇒ idle; sd_notify with a temp datagram socket; fallback calls hit the simulator with the right payloads.

### Task 7: Frontend plumbing

Files: `frontend/src/api.ts`, `frontend/src/types.ts`, `frontend/src/live.ts`, `frontend/src/App.tsx`.

- Types + API functions for every endpoint added in Tasks 1–6 (settings fields, version, setup outputs/audio/test_sound/triggers/calibration, preflight + test_all, show_lock, events + stats, board offset, fallback).
- `live.ts`: handle `preflight`, `trigger_seen`; keep `version` from snapshot.
- `App.tsx`: routes `/setup` and `/show` (placeholder components from new files `pages/Setup.tsx`, `pages/ShowMode.tsx` exporting a default component with a heading only), nav entry "Setup". When `settings.show_lock` is on, show a "Show mode on" badge in the header and hide nav entries Profiles, Media.

### Task 8: Setup page

Files: `frontend/src/pages/Setup.tsx` (+ optional `frontend/src/components/SetupStep.tsx`).

Per spec 1.2 and 1.4: steps Projector output, Audio output (device, test sound, volume, audio delay), Boards (online/adopted counts, "Check all settings" runs recommendations per board, link to Boards), Trigger devices (token, prefilled curl + ESPHome snippet with `window.location.origin` and token, live "last trigger" from WS, test button), Sync calibration (start/stop, global offset slider live, per-board offset sliders). Status dot per step. Usable at 375 px. Disabled controls + hint while a show plays.

### Task 9: Show mode page

Files: `frontend/src/pages/ShowMode.tsx`.

Full-screen phone-first page per spec 1.5/1.6: profile + progress, big Trigger / Play-Stop / Next / Blackout, brightness and volume sliders (respect caps), quick per-board panel (base / off / white via board state endpoint), preflight worst-status dot linking to `/setup`, lock toggle (asks PIN when needed). No editing UI.

### Task 10: Dashboard and Settings additions

Files: `frontend/src/pages/Dashboard.tsx`, `frontend/src/pages/Settings.tsx`.

Dashboard: preflight fail banner with link, link to `/show`, master volume slider next to brightness, History tab (events list from `/api/events`, tonight's trigger count, per-hour bars). Settings: brightness and volume caps, show-lock toggle, version footer (`commit`, `installed_at`).

### Task 11: Boards page additions

Files: `frontend/src/pages/Boards.tsx`.

Per-board offset field (ms, saved via PATCH), "Save fallback to boards" button (confirm dialog: writes board flash) with per-board result.

### Task 12: Docs and roadmap

Files: `docs/architecture.md`, `docs/features.md`, `docs/operations.md`, `docs/decisions.md`, `docs/roadmap/phase-1-show-night.md`, `docs/roadmap/README.md`, `README.md`.

Update per `AGENTS.md` for everything in Tasks 1–11; tick completed checkboxes; leave the hardware SPIKE items unticked and noted as "needs real hardware". Decisions to record: lock allowlist, fallback preset slot 250, preflight interval 10 s, calibration clip format.
