# Architecture

```
 phone / desktop browser
        │  REST /api/…  +  one WebSocket /ws (server push, UI never polls)
        ▼
 ┌──────────── spookhub.service (Python 3.13, FastAPI, asyncio) ────────────┐
 │ api/     REST routes, WS fan-out of the event bus                        │
 │ show/    ShowEngine: active profile, cue scheduler, triggers, blackout   │
 │          resolve.py: pure "what should board X show at t"                │
 │          scene, moments, sound lane, idle track, ambient, fades          │
 │ boards/  BoardManager: registry, health, desired-state reconciliation    │
 │          queue.py latest-wins mailbox · discovery.py mDNS + /24 scan     │
 │ player/  mpv supervisor + own async JSON-IPC client; main + fx + ambient │
 │ media/   streamed upload, ffprobe, thumbnail sprite, waveform, transcode │
 │ SQLite (SQLModel + Alembic) in /var/lib/spookhub                         │
 └──────┬─────────────────────────────────────────┬─────────────────────────┘
        │ JSON IPC (unix socket)                  │ HTTP JSON, concurrent
        ▼                                         ▼
  3 × mpv (main: DRM/KMS → HDMI;            WLED boards (keyed by MAC)
  fx, ambient: --no-video) → ALSA dmix
```

## Backend modules (`backend/spookhub/`)

| Path | Role |
|---|---|
| `main.py`, `hub.py` | App factory, lifespan, wires `Hub` (bus, settings, boards, player, `fx` + `ambient` effects players, engine). Audio device, volume and cap changes go to all three players |
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
| `player/process.py` | `MpvPlayer`: args (incl. `--glsl-shaders=<warp.glsl>`), supervise/restart with backoff, load/black/seek/pause. `apply_geometry` sets the target; flushed after 30 ms coalescing, diff-only against last sent props/shader opts. Re-sent before every `loadfile` (per-load engine hook) and after a restart |
| `player/geometry.py` | Pure `DisplayGeometry` → mpv props (`video-zoom/pan/scale/align/rotate/aspect-override/crop`, `brightness/contrast/gamma/saturation`) + shader opts. Crop mapped to coded source px through file rotation + `rotate` (mpv crops the coded frame first, then rotates; verified on mpv 0.41) |
| `player/shaders/warp.glsl` | `OUTPUT` hook. `//!PARAM` `c0x…c3y` (corner offsets TL,TR,BR,BL, ±0.5), `mask_t/r/b/l`, `feather` (0–0.5), `flip_h/v` (0/1). All 0 = identity. Set live via `glsl-shader-opts` (vo=gpu-next only) |
| `player/effects.py` | `EffectsPlayer(MpvPlayer)`: `--no-video`, own IPC socket. `fx` (sound cues, soundboard, moment sounds) and `ambient` (ambient loop). One sound per player, a new play supersedes an in-flight one (generation counter). `play(path, start, volume, fade_in_ms, loop, owner)`, `stop(fade_out_ms, owner)` (owner-scoped stop), `ramp(volume, ms)`. Level = master volume × gain (0–1.3), never above the volume cap |
| `player/ipc.py` | Async JSON IPC, `observe_property` for time-pos, pause, eof, path, duration |
| `show/engine.py` | `ShowEngine`: activate, trigger, play/stop/seek, scheduler, audition, blackout, test pattern, video setup mode, display resolution, setup activity, calibration, stall watch |
| `show/display.py` | Display profile precedence profile > media > global > identity; missing or invalid profile falls through. Media lookup by path/id |
| `show/patterns.py` | 12 alignment patterns (9 PNG, 3 looping 4 s mp4) from ffmpeg lavfi, cached `<data_dir>/patterns/<id>-<w>x<h>.<ext>` |
| `show/resolve.py` | Pure cue resolution (tested in isolation); skips sound cues |
| `show/sound_lane.py` | Pure sound-cue resolution (`active(cues, t)`) + `SoundLane` change detection on the scheduler tick, drives `fx` |
| `show/picker.py` | `Picker(order, rng)`: `sequential`, `shuffle` (bag, never the last one again), `weighted` (no immediate repeat when > 1 item). `preview(n)` clones RNG + bag. Used by scenes and moments |
| `show/scene.py` | `SceneRunner`: history, picked next, one-shot pin, `upcoming` (10), `update` on edit |
| `show/moments.py` | `MomentRunner`: blocker/busy/cooldown checks, variant pick, snapshot, run, restore, queue |
| `show/testpattern.py` | ffmpeg-rendered alignment grid PNG, cached in data dir |
| `show/sounds.py` | ffmpeg-rendered test sound WAV, cached in data dir |
| `show/calibration.py` | Sync calibration clip (20 s, 50 fps, white flash + 1 kHz beep every 2 s) and its flash cues |
| `show/preflight.py` | `Preflight` service: 12 checks (incl. `warp`: warn when geometry needs the shader but vo ≠ gpu-next), re-run every 10 s, publishes WS `preflight` on change only |
| `player/devices.py` | Pure parsing: DRM connectors/modes from sysfs, mpv `audio-device-list` |
| `api/routes_setup.py` | `/api/setup/*` and `/api/preflight*` |
| `api/routes_events.py` | `GET /api/events`, `/api/events/stats` |
| `api/routes_display.py`, `api/routes_patterns.py` | `/api/display/*`, `PATCH /api/media/{id}` (display assignment) |
| `api/routes_scene.py`, `api/routes_sounds.py`, `api/routes_moments.py` | `/api/scene/*`, `/api/sounds/*`, `/api/moments/*` (see [`api.md`](api.md)) |
| `lock.py` | Show-lock middleware: mutating `/api` calls get 423 unless allowlisted |
| `sdnotify.py` | `sd_notify` over `NOTIFY_SOCKET`: READY, WATCHDOG, STOPPING. No-op outside systemd |
| `media/*` | Upload (video `.mp4 .m4v .mkv .mov .webm .avi`, audio `.mp3 .wav .ogg .flac .m4a .opus`), probe (no picture stream ⇒ `kind=audio`; cover art ignored), derive (`<id>.thumbs.jpg/.json` video only, `<id>.wave.json`, `<id>.onsets.json`), transcode (video only) |
| `media/analyze.py` | Pure-Python onsets + BPM: 100 Hz RMS envelope, log-energy flux, median + 3·MAD peaks (≥ 80 ms apart), autocorrelation BPM. Output `{rate, onsets: [{t, strength}], bpm}` |
| `media/blackcheck.py` | ~1 fps (≤ 120 samples) `signalstats`; median YLOW/255 ≤ 0.07 = black. Cached `<media_dir>/<id>.black.json` |

## Data model

| Table | Key fields |
|---|---|
| `Board` | `mac` (PK), ip, name, adopted, firmware, led_count, effects, palettes, `fxdata` (WLED effect metadata strings), presets, `segments` `[{id, name, start, stop}]` from `/json/state`, last_seen, `offset_ms` (per-board sync, -500..500) |
| `Media` | id, filename, path, size, duration_s, width, height, codec, `kind` `video`/`audio`, display_profile_id? |
| `Profile` | id, name, type `light`/`video`/`scene`, sort, selection `{all, macs}`, base_state, overrides `{mac: LightState}`, media_id, idle_media_id, playback_mode `once`/`loop`/`idle_trigger`, cooldown_s, `markers` JSON `[{id, t, label, color?}]` sorted by t, display_profile_id?, `scene` (SceneSpec, scenes only), `ambient` (AmbientSpec?), `fade_ms` (0–10000, default 400), `idle_length_s` (light only, 0.5–3600, null = no idle loop) |
| `DisplayProfile` | id, name (unique, 1–100), geometry JSON (`DisplayGeometry`) |
| `Cue` | id, profile_id, start_s, duration_s?, target `{all, macs, segs?}`, state (LightState), transition_ms?, label? (≤200), color? (`#rrggbb`), `kind` `light`/`sound`, `sound` (SoundSpec, sound only), `track` `main`/`idle` |
| `Moment` | id, name (1–100), icon (≤32), color, sort, cooldown_s (0–3600), busy `ignore`/`interrupt`/`queue`, order `shuffle`/`weighted`, variants JSON (1–50) |
| `CueTemplate` | id, name, cues JSON `[{offset_s, duration_s?, lane all/first/same, state, transition_ms?, label?, color?}]` (1–500), created_at |
| `EventLog` | id, ts, level, kind (`trigger`, `board`, `player`, …), message, data JSON. Kept 7 days |
| `Setting` | key/value JSON. Key `runtime` holds `RuntimeSettings` |

`CueTarget.segs` `{mac: [seg ids]}`: on that board the cue drives only those WLED segments,
never the whole board. Absent = whole boards only.
`LightState`: preset? or explicit on, bri, fx, pal, colors (≤3 RGB), sx, ix, transition_ms.
`SceneSpec`: items `[{profile_id, weight (>0, default 1)}]` (≤200, unique, no scenes), order
`sequential`/`shuffle`/`weighted`, idle_profile_id?, chain, light_hold_s (default 10).
`AmbientSpec`: media_id (audio), volume (0–1.3), duck_to (0–1, default 0.3, fraction of volume
while the main video plays). `SoundSpec`: media_id, volume (0–1.3), fade_in_ms, fade_out_ms
(0–60000), loop. Sound cues ignore target and state.
`MomentVariant`: video_profile_id?, light_profile_id?, sound? `{media_id, volume, fade_in_ms,
fade_out_ms}`, ambience `keep`/`mute`/`replace` (+ ambience_media_id), weight. At least one part.
Revisions: `b1a0d4e5c601` (audio, sound cues) → `b2c3e4f5a602` (scenes) → `b3d4f5a6b703`
(ambient, idle track, fade) → `b4e5a6b7c804` (moments).
`RuntimeSettings`: sync_offset_ms (±2000), master_brightness (0–1), health_interval_s,
default_transition_ms, log_level, mpv_vo, mpv_gpu_context, mpv_drm_connector, mpv_drm_mode,
mpv_audio_device, master_volume (0–130), volume_cap, brightness_cap, audio_delay_s (±5),
show_lock, active_profile_id, display_profile_id (global default). Values above a cap are clamped,
and lowering a cap also lowers the current master value.

`Media.rotation` (0/90/180/270 clockwise, from the display matrix or `rotate` tag; revision `a9c4e7d2b153`; NULL rows are re-probed at startup). `display_profile_id` on `Media`/`Profile` is a plain id, no FK (revision `f3b8d1c5a274`). Deleting
a display profile clears it there and in the global setting.
`DisplayGeometry` (default = untouched picture): zoom (±2), pan_x/y (±1), scale_x/y (0.25–4),
align_x/y (±1), rotate 0/90/180/270, aspect? (0.2–10), crop `{x, y, w, h}` (fractions of the
displayed, rotated frame), brightness/contrast/gamma/saturation (±100), corners 4 × `(dx, dy)`
(±0.5 of output, TL TR BR BL), mask `{top, right, bottom, left, feather}` (0–0.5), flip_h, flip_v.

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
- **Video setup mode.** Rides on the test-pattern state (`test_pattern` true, phase
  `test_pattern`): stops a running show, refuses triggers and setup actions, 409 during blackout or
  a setup activity. Source: pattern (rendered 1920x1080), media or idle clip. `live` geometry goes
  to mpv unsaved; leaving setup (off, test pattern on, activate, blackout) drops it and restores
  idle. Outside setup the per-load hook resolves geometry for whatever mpv loads.
- **Effects players.** `fx` and `ambient` are supervised like main mpv (restart with backoff). A
  restarted one plays nothing; the sound lane re-issues the covering cue on the next tick. Blackout,
  stop and deactivate silence `fx` (soundboard too) and blackout/deactivate stop `ambient`.
- **Sound cues.** The scheduler tick resolves the covering sound cue off main `time-pos` (incl.
  audio delay) and plays it on `fx` at offset `t - start`. Latest-started covering cue wins; when it
  ends inside an older window, the older one resumes at its offset. Seek and loop wrap re-sync
  (mid fade-in at the reached gain). A cue with a duration fades out at its end; without one it
  plays to the media end (or forever when looping). Pause pauses `fx`.
- **Idle track.** `track: idle` cues loop with the idle clip's `time-pos`, or every
  `idle_length_s` on a monotonic clock (light profiles). Stopped while a main play, audition,
  setup or calibration owns the boards; restarted (full state re-sent) after.
- **Ambient.** Loops the activated profile's (or scene's, else its idle profile's) ambient on
  `ambient`. Unchanged loop keeps playing across re-activation. Main video start/end ramps it to
  `duck_to × volume` and back.
- **Idle → main fade.** Trigger away from a visible idle clip: main mpv steps a fade level 0–1
  every 20 ms over `fade_ms` (brightness → -100, volume → 0), loads the scare black and silent,
  fades back in. `fade_ms = 0` = hard cut. Anything else that loads media resets the fade.
- **Scene runner.** Scene active ⇒ its idle part (idle profile's boards, picture, idle track) runs
  under the scene id; `home_id()` stays the scene. Trigger ⇒ picker's next item: a video item plays
  once with its cues, a light item shows its base for `light_hold_s`; then back to the idle part
  without re-activation. Scene cooldown applies. `chain` starts the next item when one ends
  normally; a manual stop, blackout release or failed item pauses the chain until the next trigger.
  Deleting a profile drops it from every scene.
- **Moments.** Press ⇒ refused (409) in blackout, setup, test pattern, audition or cooldown. Busy
  (moment, main play or scene light hold): `ignore` refuses, `queue` holds one, `interrupt` ends it.
  Snapshot: active profile, timeline, phase, idle clip and ambient position. Parts run together
  (video once as the main play with its cues, sound on `fx`, light profile base on its boards
  not covered by the video, ambience keep/mute/replace); the run ends with the longest part
  (light only: `idle_length_s` or 5 s). Restore without re-activation: idle clip reloads at its
  saved position, idle track restarts, touched boards return to the home base, ambient back
  unducked. Cooldown starts at the end. An interrupting moment inherits the first snapshot.
  Blackout, activate, deactivate and test pattern abort a moment.
- **systemd watchdog.** Unit is `Type=notify`. The hub sends READY after start and WATCHDOG every
  15 s (half of `WatchdogSec=30`) from the event loop. A hung loop gets restarted.
- **Show lock.** Middleware in front of the routes. Allowlist while locked: `POST /api/trigger`,
  `/api/blackout`, `/api/show_lock`, `/api/show/{trigger,play,stop,seek,pause,next,deactivate}`,
  `/api/auth/login|logout`, `/api/boards/{mac}/state|identify`, `/api/scene/{skip,pin}`,
  `/api/sounds/{play,stop}`, `/api/moments/{id}/play`, `/api/moments/stop`,
  `PUT /api/settings` with only `master_brightness`/`master_volume`, and the OFF-only calls
  `POST /api/setup/calibration` / `/api/show/test_pattern` / `/api/display/setup` with
  `{"on": false}` (bodies ≤ 4 KB).
  `POST /api/preflight/test_all` is locked; `GET /api/preflight` is a read.
  Everything else mutating (incl. moment create/edit/delete/order) ⇒ 423. Reads stay open.

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
- Light resolution skips `kind: sound` cues. Main and idle tracks resolve separately.

## API and WebSocket

REST routes, lock allowlist codes and WS message types: [`api.md`](api.md).

## Frontend (`frontend/src/`)

React 19 + TS + Vite + Tailwind 4, built to `frontend/dist`, served by FastAPI.
`pages/` one file per route · `timeline/` editor (model, view, strip, cue panel, templates) ·
`video/` projector setup · `components/` shared UI · `api.ts` REST client · `live.ts` WS state · `types.ts` shared types.

`timeline/` modules:

| File | Role |
|---|---|
| `model.ts` | `TLCue`, lanes, undo history over `Doc {cues, markers}`. Lane ids: `__all__`, `__sound__`, `mac`, `mac#seg`. Set mode `amend` replaces the last step (panel edits within 800 ms = one undo step) |
| `resolve.ts` | Client mirror of `show/resolve.py`; shared JSON fixtures run on both sides |
| `timeops.ts` | Ripple, insert/delete time, `mm:ss.ff` timecode, clipboard (`spookhub.clipboard`, `{v: 1, cues}`) |
| `generators.ts` | Parametric cue generators (client-side) |
| `fxdata.ts` | Parse WLED fxdata: which sliders, palette, color slots an effect uses |
| `TimelineView`, `Minimap`, `MarkersLane`, `CuePanel` | Rendering and editing |
| `tracks.ts` | Main/idle track split + merge (PUT sends both tracks), `LoopClock` stand-in playhead for light idle loops |
| `SoundPickDialog` | Pick an audio media for a new sound cue |
| `GenerateDialog`, `TemplateDialog`, `RemapDialog` | Generators, saved templates, lane remap on paste |

Editor FPS is fixed at 25. Tests: `npm test` (vitest).

`video/` modules (projector setup page):

| File | Role |
|---|---|
| `geometry.ts` | Identity, normalize, group reset, Heckbert square-to-quad + inverse warp, `videoRect` (mirror of mpv placement) |
| `Preview.tsx` | WebGL preview of geometry + warp; corner handles drag keystone. Projector stays the truth |
| `patterns.ts` | Canvas stand-ins, replaced by the hub render (`/api/display/patterns/{id}/file`) for image patterns |
| `Controls.tsx`, `ProfileBar.tsx` | Geometry sliders (arrow-key nudge), display profile save/rename/delete/default/media/A-B |
| `hooks.ts` | `useThrottled` (30 ms live sends), `useDisplayState` (WS `display`, one GET until first message) |
