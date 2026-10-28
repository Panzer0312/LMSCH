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
| `events.py` | In-process pub/sub. `event()` = human log (last 50), `publish()` = typed WS message |
| `models.py` | SQLModel tables + Pydantic I/O models |
| `db.py`, `migrations/` | SQLite session, Alembic revisions shipped in package |
| `auth.py` | Optional PIN → year-long session cookie; trigger token |
| `backup.py` | Zip of DB + config; restore staged, swapped at startup |
| `boards/client.py` | WLED HTTP client (`/json/si`, `/json/state`, `/json/cfg`, presets) |
| `boards/registry.py` | `BoardManager`: health loop, status, desired state layers, reconcile |
| `boards/queue.py` | Per-board latest-wins mailbox, cue vs normal retry policy |
| `boards/discovery.py` | mDNS `_wled._tcp` browse + subnet scan |
| `boards/recommend.py` | Settings checks (today: WiFi sleep) + fixes via `/json/cfg` |
| `player/process.py` | `MpvPlayer`: args, supervise/restart with backoff, load/black/seek/pause |
| `player/ipc.py` | Async JSON IPC, `observe_property` for time-pos, pause, eof, path, duration |
| `show/engine.py` | `ShowEngine`: activate, trigger, play/stop/seek, scheduler, audition, blackout, test pattern |
| `show/resolve.py` | Pure cue resolution (tested in isolation) |
| `show/testpattern.py` | ffmpeg-rendered alignment grid PNG, cached in data dir |
| `media/*` | Upload, probe, derive (`<id>.thumbs.jpg/.json`, `<id>.wave.json`), transcode |

## Data model

| Table | Key fields |
|---|---|
| `Board` | `mac` (PK), ip, name, adopted, firmware, led_count, effects, palettes, presets, last_seen |
| `Media` | id, filename, path, size, duration_s, width, height, codec |
| `Profile` | id, name, type `light`/`video`, sort, selection `{all, macs}`, base_state, overrides `{mac: LightState}`, media_id, idle_media_id, playback_mode `once`/`loop`/`idle_trigger`, cooldown_s |
| `Cue` | id, profile_id, start_s, duration_s?, target `{all, macs}`, state (LightState), transition_ms? |
| `Setting` | key/value JSON. Key `runtime` holds `RuntimeSettings` |

`LightState`: preset? or explicit on, bri, fx, pal, colors (≤3 RGB), sx, ix, transition_ms.
`RuntimeSettings`: sync_offset_ms, master_brightness (0–1), health_interval_s,
default_transition_ms, log_level, mpv_vo, mpv_gpu_context, active_profile_id.

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
- **mpv** is a supervised child; auto-restart with backoff. Death mid-show ⇒ boards to base, engine idle.

## Cue rules

- Cue with duration is active in its window. Without duration it holds until a later cue starts on
  that board.
- Most recently started active cue wins. "All selected" and board cues follow the same rule.
- Short cue ending inside a longer one ⇒ longer resumes. Nothing active ⇒ profile base state.
- Seek recomputes state at new position; cues in between are not replayed.

## API

REST under `/api` (OpenAPI at `/docs`). Routers in `api/routes_*.py`:

| Router | Routes |
|---|---|
| auth | `GET status`, `POST login/logout`, `PUT pin`, `GET/PUT trigger_token` |
| boards | list, `scan`, `add`, `{mac}/adopt`, PATCH, DELETE, `identify`, `state`, `refresh`, `capture`, `recommendations[/{id}/apply]` |
| profiles | CRUD, `duplicate`, `activate`, `PUT {pid}/cues`, `export`, `import` |
| media | list, upload, `transcode`, `thumbs`, `thumbs.jpg`, `waveform`, `file` (range), DELETE |
| show | `GET state`, `POST trigger` (external, token), `show/trigger|play|stop|seek|pause|next|deactivate`, `blackout`, `GET/PUT settings`, `audition[/stop]`, `profiles/{pid}/play_from` |
| admin | `show/test_pattern`, `GET backup`, `POST backup/restore` |

## WebSocket `/ws`

Message `{type, data, ts}`. Types: `snapshot` (on connect), `board`, `board_removed`,
`boards_changed`, `show`, `settings`, `event`, `profiles_changed`, `media_changed`, `transcode`.
Frontend reducer: `frontend/src/live.ts`.

## Frontend (`frontend/src/`)

React 19 + TS + Vite + Tailwind 4, built to `frontend/dist`, served by FastAPI.
`pages/` one file per route · `timeline/` editor (model, view, strip, cue panel, templates) ·
`components/` shared UI · `api.ts` REST client · `live.ts` WS state · `types.ts` shared types.
