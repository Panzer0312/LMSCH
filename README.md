# Spookhub

Self-hosted Halloween controller. It plays a video on a projector with mpv and keeps several
WLED LED boards (ESP32) in sync over WiFi. Everything runs on one small Debian box. You control it
from a phone or desktop browser.

**Status: Milestones 1 and 2.** Board discovery and health, light profiles, video profiles, the
time-pos-driven cue scheduler, dashboard with trigger and blackout, installer and systemd (M1).
Visual timeline editor with waveform, thumbnails, drag editing, live audition and capture from
board (M2). Polish items (M3) are not built yet.

## Timeline editor

Open a video profile and choose "Open timeline editor". It works best on a desktop. On a phone it
opens read-only.

- **Lanes.** One "All selected" lane on top, then one lane per board in the profile.
- **Editing.** Drag a cue to move it, drag its edges to resize. Double-click empty lane space to add
  a cue. Shift or Ctrl-click to multi-select. Cues snap to other cues and to the playhead; hold Alt
  to drag without snapping. Ctrl + mouse wheel zooms.
- **Keyboard.** Space plays or pauses. Arrow keys step 0.1 s, Shift + arrow steps one frame. C adds
  a cue at the playhead. Delete removes, Ctrl+D duplicates, Ctrl+Z and Ctrl+Shift+Z undo and redo.
- **Overlaps.** Cues that overlap on a board get a warning badge.
- **Live audition.** With the toggle on, the browser preview drives the real boards. The browser
  reports its playhead every second and on play, pause and seek; the backend interpolates between
  reports. If the tab closes, the boards return to base after 3 s.
- **Capture from board.** Tweak an effect in the WLED UI, then grab the board's live state into the
  selected cue.
- **Play on projector from here.** Starts the real show on the hub at the playhead position.
- **Autosave.** Changes save 0.8 s after the last edit. The indicator shows the save state.

## Architecture

```
 phone / desktop browser
        │  REST /api/…  +  one WebSocket /ws (server push, UI never polls)
        ▼
 ┌──────────────── spookhub.service (Python 3.13, FastAPI, asyncio) ────────────────┐
 │  api/        REST routes, WebSocket fan-out of the event bus                      │
 │  show/       ShowEngine: active profile, cue scheduler, triggers, blackout        │
 │              resolve.py: pure "what should board X show at time t" logic           │
 │  boards/     BoardManager: registry, health checks, desired-state reconciliation   │
 │              queue.py: per-board latest-wins mailbox · discovery.py: mDNS + scan   │
 │  player/     mpv supervisor + own async JSON-IPC client                            │
 │  media/      upload (streamed to disk), ffprobe, thumbnail sprite + waveform        │
 │  SQLite (SQLModel + Alembic) in /var/lib/spookhub                                 │
 └───────┬──────────────────────────────────────────────┬──────────────────────────────┘
         │ JSON IPC (unix socket)                       │ HTTP JSON API, concurrent
         ▼                                              ▼
   mpv (DRM/KMS → HDMI projector, ALSA audio)     WLED boards (identified by MAC)
```

### Reliability design

- **Desired state.** Every command first updates what a board *should* show, then tries delivery.
  Layers, highest first: blackout, identify blink, cue or profile base, manual override.
  Master brightness is applied as a multiplier when the payload is built.
- **Reconciliation.** Health checks call `GET /json/si` every 3 s. A board that comes back online,
  or turns up at a new IP via mDNS or scan (matched by MAC), gets its full desired state re-sent.
  The UI shows "reconnected, state restored".
- **Latest-wins queue per board.** At most one pending command per board. A newer command replaces
  an older unsent one. Cue commands get a 300 ms timeout and one retry. Other commands back off
  exponentially. One slow board never delays the others.
- **Scheduler.** Runs every 10 ms off mpv's real `time-pos`, interpolated with a monotonic clock
  and capped so a stalled player can't run ahead. Per board it adds latency compensation (half the
  measured round trip, capped) plus the global offset, resolves the active cue, and sends only when
  the result changes. Cue starts, cue ends, loop wrap-around and seeks all use this one path.
- **Presets.** WLED applies `ps` asynchronously, which would overwrite a brightness sent in the same
  request. Spookhub therefore expands presets from the cached `presets.json` into explicit state, so
  master brightness works with presets too.
- **mpv.** Supervised child process with auto-restart and backoff. If it dies mid-show, the boards
  return to base and the engine goes back to idle.

### Cue rules

- A cue with a duration is active during its window. Without a duration it holds until any later
  cue starts on that board.
- The active cue that started most recently wins. "All selected boards" cues and board-specific
  cues follow the same rule.
- When a short cue ends inside a longer cue's window, the longer one resumes. With nothing active,
  the board shows the profile's base state.
- Seeking recomputes the state at the new position without replaying cues in between.

### Decisions where the spec was open

| Topic | Choice |
|---|---|
| Trigger cooldown | Counted from the end of the previous play. Triggers while playing are ignored. |
| Trigger in once/loop mode | Rejected. Use Play/Stop there. |
| Blackout release | Re-activates the current profile from the start. |
| Manual board overrides | Cleared on the next profile activation. |
| Health check | Polling `/json/si`; the WLED WebSocket is not used (one code path, robust). |
| Audio | ALSA directly. PipeWire needs a user session, which a system service doesn't have. |
| Boards outside the new profile's selection | Left as they are on profile switch. |

## Development

Requirements: Python 3.13 via [uv](https://docs.astral.sh/uv/), Node 20+, mpv, ffmpeg.

```bash
cd backend && uv sync          # Python deps (pins 3.13 via .python-version)
cd frontend && npm install
scripts/dev.sh                 # 3 simulated boards + backend + windowed mpv + Vite on :5173
scripts/dev.sh --no-mpv        # without the video player
```

In the UI, open Boards and add `127.0.0.1:9001`, `:9002`, `:9003` by IP, then adopt them.

### WLED simulator

```bash
cd backend && uv run python ../simulator/wled_sim.py --boards 4 --base-port 9001
curl -X POST 'localhost:9001/sim/latency?ms=120'    # simulate slow WiFi
curl -X POST 'localhost:9001/sim/dropout?on=true'   # board hangs, then 503
curl localhost:9001/sim/log                         # received commands with timestamps
```

### Tests and checks

```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
cd frontend && npx tsc -b && npm run lint
```

The end-to-end test runs real mpv with null output against simulated boards. It asserts every
cue arrives within ±100 ms of its video timestamp and prints the measured error.

## Deploy to the mini PC

1. Install Debian 13 minimal with SSH. Create a user with sudo.
2. From the dev machine, first time:
   ```bash
   scripts/deploy.sh user@minipc --install
   ```
   This builds the frontend, rsyncs the project and runs `scripts/install.sh` on the hub. The
   installer is idempotent. It installs packages, creates the `spookhub` user (groups video, render,
   audio), the venv, `/etc/spookhub/config.toml`, the systemd unit, disables console blanking and
   masks sleep targets. It prints the URL at the end.
3. Later updates: `scripts/deploy.sh user@minipc` (sync, reinstall package, restart).
4. Reboot once so the `consoleblank=0` kernel option takes effect.

Paths: code `/opt/spookhub`, config `/etc/spookhub/config.toml`, data `/var/lib/spookhub`.
Logs: `journalctl -u spookhub -f`. Every config key can be overridden with an env var such as
`SPOOKHUB_MPV__EXTRA_ARGS='["--drm-connector=HDMI-A-1"]'`.

## Hardware tips

- **Video content.** Use pure black backgrounds. Black emits no light, so on a scrim or window
  only the ghost appears.
- **Projection surface.** Grey scrim, voile curtain, or a window with rear-projection film. Project
  from behind or inside so the projector is hidden.
- **Static IPs.** Reserve a DHCP lease per WLED board in the router. Spookhub follows IP changes by
  MAC, but a reservation avoids the gap.
- **WiFi sleep.** Disable it on every board in WLED: Config, WiFi Setup, "Disable WiFi sleep".
  Otherwise latency jumps to hundreds of ms.
- **Codecs.** H.264 or HEVC at 1080p or below decodes in hardware on Intel mini PCs.
- **New boards.** Flash with the [WLED web installer](https://install.wled.me), join the `WLED-AP`
  access point, enter WLAN credentials at 4.3.2.1. The board appears under Discovered.

## Troubleshooting

| Symptom | Check |
|---|---|
| Black screen, no video | `journalctl -u spookhub -e` for mpv errors. Run `vainfo`. Try `extra_args = ["--drm-connector=HDMI-A-1"]` or `hwdec = "no"`. |
| "mpv exited, restarting" loop | Another process holds the DRM device (a desktop or getty with KMS). Make sure the user is in `video` and `render`. |
| No sound | `aplay -l`, then set `extra_args = ["--audio-device=alsa/hdmi:CARD=PCH,DEV=0"]` or the analog device. |
| Board stays offline | Ping it. Check the router lease. Use Boards, Scan network, or add by IP. |
| Lights lag the video | Disable WiFi sleep. Increase the global sync offset in Settings. |
| Board not discovered | mDNS is often blocked on guest or mesh WiFi. Use Scan network or add by IP. |
| Socket errors in dev | Unix socket paths are limited to 108 bytes. Spookhub falls back to `/tmp` for long paths. |
