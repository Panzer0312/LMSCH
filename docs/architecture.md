# Architecture

```
 phone / desktop browser
        │  REST /api/…  +  one WebSocket /ws (server push, UI never polls)
        ▼
 ┌──────────── spookhub.service (Python 3.13, FastAPI, asyncio) ────────────┐
 │ api/     REST routes, WS fan-out of the event bus                        │
 │ show/    ShowEngine: active profile, cue scheduler, triggers, blackout   │
 │          resolve.py: pure "what should board X show at t"                │
 │ boards/  BoardManager: registry, health, desired-state reconciliation    │
 │          queue.py latest-wins mailbox · discovery.py mDNS + /24 scan     │
 │ player/  mpv supervisor + own async JSON-IPC client                      │
 │ media/   streamed upload, ffprobe, thumbnail sprite, waveform, transcode │
 │ SQLite (SQLModel + Alembic) in /var/lib/spookhub                         │
 └──────┬─────────────────────────────────────────┬─────────────────────────┘
        │ JSON IPC (unix socket)                  │ HTTP JSON, concurrent
        ▼                                         ▼
  mpv (DRM/KMS → HDMI, ALSA audio)          WLED boards (keyed by MAC)
```

## Backend modules (`backend/spookhub/`)

| Path | Role |
|---|---|
| `main.py`, `hub.py` | App factory, lifespan, wires `Hub` (bus, settings, boards, player, engine) |
| `config.py` | TOML (`/etc/spookhub/config.toml`) + `SPOOKHUB_*` env overrides, `__` nesting |
| `settings_store.py` | `RuntimeSettings` as one JSON DB row, editable live, listeners on change |
| `events.py` | In-process pub/sub. `event(kind=)` = human log (last 50), `publish()` = typed WS message. `EventWriter` batches events into `EventLog` (1 s flush, 7-day retention, prune every 6 h) |
| `models.py` | SQLModel tables + Pydantic I/O models |
| `db.py`, `migrations/` | SQLite session, Alembic revisions shipped in package |
| `auth.py` | Optional PIN → year-long session cookie; trigger token |
| `backup.py` | Zip of DB + config; restore staged, swapped at startup |
| `boards/client.py` | WLED HTTP client (`/json/si`, `/json/state`, `/json/cfg`, presets) |
| `boards/registry.py` | `BoardManager`: health loop, status, desired state layers, reconcile |
| `boards/queue.py` | Per-board latest-wins mailbox, cue vs normal retry policy |
| `boards/discovery.py` | mDNS `_wled._tcp` browse + subnet scan |
| `boards/recommend.py` | Settings checks (today: WiFi sleep) + fixes via `/json/cfg`. Result cached on the board runtime (6 h) for preflight |
| `player/process.py` | `MpvPlayer`: args, supervise/restart with backoff, load/black/seek/pause |
| `player/ipc.py` | Async JSON IPC, `observe_property` for time-pos, pause, eof, path, duration |
| `show/engine.py` | `ShowEngine`: activate, trigger, play/stop/seek, scheduler, audition, blackout, test pattern, setup activity, calibration, stall watch |
| `show/resolve.py` | Pure cue resolution (tested in isolation) |
| `show/testpattern.py` | ffmpeg-rendered alignment grid PNG, cached in data dir |
| `show/sounds.py` | ffmpeg-rendered test sound WAV, cached in data dir |
| `show/calibration.py` | Sync calibration clip (20 s, 50 fps, white flash + 1 kHz beep every 2 s) and its flash cues |
| `show/preflight.py` | `Preflight` service: 11 checks, re-run every 10 s, publishes WS `preflight` on change only |
| `player/devices.py` | Pure parsing: DRM connectors/modes from sysfs, mpv `audio-device-list` |
| `api/routes_setup.py` | `/api/setup/*` and `/api/preflight*` |
| `api/routes_events.py` | `GET /api/events`, `/api/events/stats` |
| `lock.py` | Show-lock middleware: mutating `/api` calls get 423 unless allowlisted |
| `sdnotify.py` | `sd_notify` over `NOTIFY_SOCKET`: READY, WATCHDOG, STOPPING. No-op outside systemd |
| `media/*` | Upload, probe, derive (`<id>.thumbs.jpg/.json`, `<id>.wave.json`, `<id>.onsets.json`), transcode |
| `media/analyze.py` | Pure-Python onsets + BPM: 100 Hz RMS envelope, log-energy flux, median + 3·MAD peaks (≥ 80 ms apart), autocorrelation BPM. Output `{rate, onsets: [{t, strength}], bpm}` |

## Data model

| Table | Key fields |
|---|---|
| `Board` | `mac` (PK), ip, name, adopted, firmware, led_count, effects, palettes, `fxdata` (WLED effect metadata strings), presets, `segments` `[{id, name, start, stop}]` from `/json/state`, last_seen, `offset_ms` (per-board sync, -500..500) |
| `Media` | id, filename, path, size, duration_s, width, height, codec |
| `Profile` | id, name, type `light`/`video`, sort, selection `{all, macs}`, base_state, overrides `{mac: LightState}`, media_id, idle_media_id, playback_mode `once`/`loop`/`idle_trigger`, cooldown_s, `markers` JSON `[{id, t, label, color?}]` sorted by t |
| `Cue` | id, profile_id, start_s, duration_s?, target `{all, macs, segs?}`, state (LightState), transition_ms?, label? (≤200), color? (`#rrggbb`) |
| `CueTemplate` | id, name, cues JSON `[{offset_s, duration_s?, lane all/first/same, state, transition_ms?, label?, color?}]` (1–500), created_at |
| `EventLog` | id, ts, level, kind (`trigger`, `board`, `player`, …), message, data JSON. Kept 7 days |
| `Setting` | key/value JSON. Key `runtime` holds `RuntimeSettings` |

`CueTarget.segs` `{mac: [seg ids]}`: on that board the cue drives only those WLED segments,
never the whole board. Absent = whole boards only.
`LightState`: preset? or explicit on, bri, fx, pal, colors (≤3 RGB), sx, ix, transition_ms.
`RuntimeSettings`: sync_offset_ms (±2000), master_brightness (0–1), health_interval_s,
default_transition_ms, log_level, mpv_vo, mpv_gpu_context, mpv_drm_connector, mpv_drm_mode,
mpv_audio_device, master_volume (0–130), volume_cap, brightness_cap, audio_delay_s (±5),
show_lock, active_profile_id. Values above a cap are clamped, and lowering a cap also lowers the
current master value.

## Reliability design

- **Desired state.** Each command updates what a board *should* show, then delivers. Layers,
  highest first: blackout, identify blink, cue or profile base, manual override. Master brightness
  multiplies at payload build.
- **Reconciliation.** Health = `GET /json/si` every 3 s. Board back online, or seen at a new IP
  (mDNS/scan, matched by MAC) ⇒ full desired state re-sent; UI event "reconnected, state restored".
- **Latest-wins queue.** One pending command per board; newer replaces unsent. Cue commands: 300 ms
  timeout + one retry. Others: exponential backoff. Boards never block each other.
- **Scheduler.** 10 ms tick off mpv `time-pos`, interpolated by monotonic clock, capped
  (0.5 s) so a stalled player can't run ahead. Per board: + half measured RTT (capped 150 ms) + global
  offset, resolve active cue, send only on change. Cue start/end, loop wrap, seek share one path.
- **Presets** are expanded from cached `presets.json` into explicit state, because WLED applies `ps`
  async and would override brightness sent in the same request.
- **mpv** is a supervised child; auto-restart with backoff. Death mid-show ⇒ resume at the last
  position (counts as the play's one recovery, shared with the stall watch); a second failure ⇒
  boards to base, engine idle. Restart outside a show re-applies the phase picture (idle clip,
  test pattern, calibration clip or black).
- **Stall watch.** During a main show, `time-pos` frozen ≥ 2 s (not paused, seeking, buffering or
  loading) ⇒ restart mpv and resume at the last position. A second failure in the same play ⇒ idle.
- **Setup activity.** Test sound, calibration and test-all run as one cancellable setup task.
  Triggers, plays, test pattern and audition get 409 while it runs. Blackout, activate and
  deactivate cancel it. On end the engine restores idle (profile base, idle clip or black).
  Turning the show lock on cancels it too and turns the test pattern off. Blackout turns the test
  pattern off; test pattern on during blackout ⇒ 409. `PUT /api/settings` changing an `mpv_*`
  output/audio key ⇒ 409 while a show plays or a setup action runs (it restarts mpv).
- **systemd watchdog.** Unit is `Type=notify`. The hub sends READY after start and WATCHDOG every
  15 s (half of `WatchdogSec=30`) from the event loop. A hung loop gets restarted.
- **Show lock.** Middleware in front of the routes. Allowlist while locked: `POST /api/trigger`,
  `/api/blackout`, `/api/show_lock`, `/api/show/{trigger,play,stop,seek,pause,next,deactivate}`,
  `/api/auth/login|logout`, `/api/boards/{mac}/state|identify`,
  `PUT /api/settings` with only `master_brightness`/`master_volume`, and the OFF-only calls
  `POST /api/setup/calibration` / `POST /api/show/test_pattern` with `{"on": false}` (bodies ≤ 4 KB).
  `POST /api/preflight/test_all` is locked; `GET /api/preflight` is a read.
  Everything else mutating ⇒ 423. Reads stay open.

## Cue rules

- Cue with duration is active in its window. Without duration it holds until a later cue starts on
  that board.
- Most recently started active cue wins. "All selected" and board cues follow the same rule.
- Short cue ending inside a longer one ⇒ longer resumes. Nothing active ⇒ profile base state.
- Seek recomputes state at new position; cues in between are not replayed.
- Segments: the whole-board state ignores segment cues. A segment `(mac, seg)` resolves by the
  same latest-start rule over the board's whole-board cues plus that segment's cues. A later
  whole-board cue covers an earlier segment cue and vice versa.
- `Timeline.board_at` returns the whole-board state plus states only for segments whose own
  segment cue wins. Change key = (board cue id, ((seg, cue id), …)).
- `boards/client.py` sends one `/json/state` body per board: board state plus
  `seg: [{id, fx, pal, sx, ix, col}]`. `on`, `bri` and transition are board-wide in WLED and come
  from the board state.
- Audition `muted` MACs are held at the profile base.

## API

REST under `/api` (OpenAPI at `/docs`). Routers in `api/routes_*.py`:

| Router | Routes |
|---|---|
| auth | `GET status`, `POST login/logout`, `PUT pin`, `GET/PUT trigger_token` |
| boards | list, `scan`, `add`, `{mac}/adopt`, PATCH (`name`, `offset_ms`), DELETE, `identify`, `POST state`, `DELETE state` (clear manual override), `refresh`, `capture`, `recommendations[/{id}/apply]`, `POST fallback`, `POST {mac}/fallback` |
| profiles | CRUD, `duplicate`, `activate`, `PUT {pid}/cues`, `PUT {pid}/markers` (replace all, ids unique), `export`, `import` |
| templates | `GET templates`, `POST templates {name, cues}`, `DELETE templates/{tid}` |
| media | list, upload, `transcode`, `thumbs`, `thumbs.jpg`, `waveform`, `{mid}/onsets` (empty list if not derived), `file` (range), DELETE |
| show | `GET state`, `POST trigger` (external, token), `show/trigger|play|stop|seek|pause|next|deactivate`, `blackout`, `GET/PUT settings`, `audition[/stop]` (body adds `muted: [mac]`), `profiles/{pid}/play_from` |
| setup | `GET setup/outputs`, `GET setup/audio_devices`, `POST setup/test_sound`, `POST setup/calibration {on}`, `GET setup/triggers`, `GET preflight`, `POST preflight/test_all` |
| events | `GET events?since&kind&limit`, `GET events/stats?since` (triggers total + per hour) |
| admin | `show/test_pattern`, `POST show_lock {on, pin?}`, `GET backup`, `POST backup/restore` |

`GET /api/state` includes `version` (commit, install time). Setup actions return 409 while a show
plays or another setup action runs, 503 when mpv is unavailable. Fallback returns
`{results: [{mac, name, ok, error}]}`.

## WebSocket `/ws`

Message `{type, data, ts}`. Types: `snapshot` (on connect), `board`, `board_removed`,
`boards_changed`, `show`, `settings`, `event`, `profiles_changed`, `media_changed`, `transcode`,
`preflight` (`{checks, worst}`), `trigger_seen` (`last_trigger {ts, source, accepted, message}`).
The snapshot also carries `version` and `preflight`. Show snapshot adds `calibrating`, `setup_busy`, `last_trigger`.
Frontend reducer: `frontend/src/live.ts`.

## Frontend (`frontend/src/`)

React 19 + TS + Vite + Tailwind 4, built to `frontend/dist`, served by FastAPI.
`pages/` one file per route · `timeline/` editor (model, view, strip, cue panel, templates) ·
`components/` shared UI · `api.ts` REST client · `live.ts` WS state · `types.ts` shared types.

`timeline/` modules:

| File | Role |
|---|---|
| `model.ts` | `TLCue`, lanes, undo history over `Doc {cues, markers}`. Lane ids: `__all__`, `mac`, `mac#seg`. Set mode `amend` replaces the last step (panel edits within 800 ms = one undo step) |
| `resolve.ts` | Client mirror of `show/resolve.py`; shared JSON fixtures run on both sides |
| `timeops.ts` | Ripple, insert/delete time, `mm:ss.ff` timecode, clipboard (`spookhub.clipboard`, `{v: 1, cues}`) |
| `generators.ts` | Parametric cue generators (client-side) |
| `fxdata.ts` | Parse WLED fxdata: which sliders, palette, color slots an effect uses |
| `TimelineView`, `Minimap`, `MarkersLane`, `CuePanel` | Rendering and editing |
| `GenerateDialog`, `TemplateDialog`, `RemapDialog` | Generators, saved templates, lane remap on paste |

Editor FPS is fixed at 25. Tests: `npm test` (vitest).
