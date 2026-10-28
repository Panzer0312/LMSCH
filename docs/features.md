# Features

## Pages

| Route | Page | Does |
|---|---|---|
| `/` | Dashboard (phone-first) | Active profile + progress, Trigger, Play/Stop, Next, master brightness, board health strip, last 20 events |
| `/profiles[/:id]` | Profiles | List, create, edit, duplicate, delete, activate. Light or video type, board selection or "All boards", base state + per-board overrides, playback mode, idle video, cooldown, cue list form |
| `/profiles/:id/timeline` | Timeline editor | See below. Desktop-first, read-only on phone |
| `/boards` | Boards | Discovered vs adopted, status, RSSI, latency, firmware, LEDs, live state. Rename, identify, on/off, brightness, open WLED UI, refresh, remove, scan, add by IP, "Check settings", new-board guide |
| `/media` | Media | Upload with progress, probe info, transcode, used-by, delete (blocked if used) |
| `/settings` | Settings | Sync offset, health interval, default transition, log level, mpv output, test pattern, PIN, trigger token, backup/restore |

Blackout button is in the header on every page. Stops video, blacks projector, boards off, held until released.

## Profiles and playback

- **Light profile:** applies base states to its boards.
- **Video profile:** video + cues + mode `once`, `loop` or `idle_trigger` (idle video or black between
  triggered plays, with cooldown). End or stop ⇒ boards to base, idle video or black.
- One profile active at a time. Switch stops video and applies new base states.

## Timeline editor

- **Lanes:** "All selected" on top, one lane per board in the profile.
- **Editing:** drag to move, drag edges to resize, double-click empty lane to add. Shift/Ctrl-click
  multi-select. Snap to cues and playhead; Alt disables snap. Ctrl + wheel zooms.
- **Keys:** Space play/pause · ←/→ 0.1 s · Shift+←/→ one frame · C add cue at playhead · Delete ·
  Ctrl+D duplicate · Ctrl+Z / Ctrl+Shift+Z undo/redo.
- **Overlaps** on a board get a warning badge.
- **Live audition:** browser preview drives real boards. Browser reports playhead every 1 s and on
  play/pause/seek; backend interpolates. Tab closes ⇒ boards to base after 3 s. Rejected while a show plays.
- **Capture from board:** pull a board's live WLED state into the selected cue.
- **Play on projector from here:** real show from the playhead.
- **Autosave** 0.8 s after last edit, with indicator.
- **Templates:** Flicker, Strobe, Blackout, Blood red, Lightning flash. Add more in
  `frontend/src/timeline/templates.ts`.

## Operations

- **Recommended settings:** reads `/json/cfg`, offers confirmed one-click fix. Check: WiFi sleep off.
- **Transcode** to H.264 1080p for non-H.264/HEVC or >1080p. Media id kept, profiles keep working.
- **Export/import** profile JSON with cues. Video matched by filename or picked on import. Unknown board targets dropped.
- **UI PIN:** all API + WS need session cookie (1 year). Changing/removing PIN logs everyone out. Off by default.
- **External trigger:** `POST /api/trigger`, outside the PIN, optional token via `?token=` or
  `X-Trigger-Token`. PIR examples in `docs/trigger/`.
  ```bash
  curl -X POST http://spookhub.local:8080/api/trigger -H "X-Trigger-Token: <token>"
  ```
- **Test pattern:** alignment grid on projector. Stops any show; off ⇒ back to active profile idle.
- **Backups:** zip of DB + config, no media. Restore stages DB and restarts. Restored config goes to
  `/var/lib/spookhub/config.restored.toml` (since `/etc` is read-only for the service), copy by hand.
