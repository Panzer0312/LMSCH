# Features

## Pages

| Route | Page | Does |
|---|---|---|
| `/` | Dashboard (phone-first) | Preflight banner, active profile + progress, Trigger, Play/Stop, Next, master brightness, board health strip, last 20 events, History, link to `/show` |
| `/profiles[/:id]` | Profiles | List, create, edit, duplicate, delete, activate. Light or video type, board selection or "All boards", base state + per-board overrides, playback mode, idle video, cooldown, cue list form |
| `/profiles/:id/timeline` | Timeline editor | See below. Desktop-first, read-only on phone |
| `/boards` | Boards | Discovered vs adopted, status, RSSI, latency, firmware, LEDs, live state. Rename, identify, on/off, brightness, sync offset (ms), "Save fallback" per board and for all, open WLED UI, refresh, remove, scan, add by IP, "Check settings", new-board guide |
| `/setup` | Setup | Five steps with status dots, see below. Phone-friendly |
| `/show` | Show mode | Full-screen big buttons for the night, see below |
| `/media` | Media | Upload with progress, probe info, transcode, used-by, delete (blocked if used) |
| `/settings` | Settings | Sync offset, health interval, default transition, log level, mpv output, brightness and volume caps, show mode toggle, test pattern, PIN, trigger token, backup/restore. Footer shows deployed commit and install time |

Blackout button is in the header on every page. Stops video, blacks projector, boards off, held until released.

## Setup page

| Step | Does |
|---|---|
| Projector output | DRM connector (connected first) and mode dropdowns. Applying restarts mpv (screen black ~1 s). Test pattern button |
| Audio output | Device dropdown (friendly names), volume, audio delay, "Play test sound", reload devices |
| Boards | Online/adopted count, "Check all settings", link to Boards |
| Trigger devices | Token, curl and ESPHome snippets with hub URL, live "waiting for trigger" light, "Send test trigger" |
| Sync calibration | Loops a clip on the projector; boards flash white on the beat. Global offset and per-board offset sliders apply live. Move until flashes match |

The page also lists the preflight checks. "Test everything" flashes all boards white 2 s, plays the
test sound, shows the pattern 2 s, then restores idle. Setup actions are disabled while a show plays
or show mode is on.

## Preflight

Eleven checks, each ok, warn or fail, with a fix link: boards online, latency (< 100 ms), WiFi sleep,
mpv, audio device, active profile media, disk (> 2 GB), clock sync, mpv restarts last hour, recent
trigger (warn only, `idle_trigger` profiles, 30 min, accepted external triggers only), no setup
running (fail while calibration, a setup test or the test pattern is on). Re-run every 10 s, pushed on change. The
Dashboard banner shows when any check fails.

## Show mode

- **Lock** (`show_lock`, toggle on `/show` or Settings): editing endpoints return 423 and the UI
  shows "Show mode is on". Show controls stay open. Unlocking needs the PIN when one is set.
  Turning the lock on stops calibration, test sound, test-all and the test pattern.
- **`/show` page:** Trigger, Play/Stop, Next, Blackout, brightness and volume sliders (limited by
  the caps), current profile + progress, preflight dot, per-board Base / Off / White buttons.
  "Base" clears the manual override so the profile look returns. A red banner shows while
  calibration, a setup test or the test pattern runs (triggers are refused); its "Stop" turns
  calibration / test pattern off, also under the lock.
- **Caps:** `brightness_cap` and `volume_cap` limit the master sliders everywhere.

## History

Dashboard "History": triggers tonight (since local noon), triggers per hour bars, last 100 events
from the persistent log. Events survive restarts and are kept 7 days.

## Profiles and playback

- **Light profile:** applies base states to its boards.
- **Video profile:** video + cues + mode `once`, `loop` or `idle_trigger` (idle video or black between
  triggered plays, with cooldown). End or stop ⇒ boards to base, idle video or black.
- One profile active at a time. Switch stops video and applies new base states.

## Timeline editor

- **Lanes:** "All selected" on top, one lane per board in the profile. Board lane header shows the
  resolved color + effect at the playhead; a ribbon under the lane shows the resolved color over time.
- **Overview bar** above the lanes: cues, markers, playhead, visible window. Click or drag to scroll.
- **Ruler:** m:ss; zoomed in far enough it switches to `mm:ss.ff` frame ticks (25 fps).
- **Editing:** drag to move, drag edges to resize, double-click empty lane to add. Shift/Ctrl-click
  multi-select. Snap to cue edges, playhead, markers and visible onsets; Alt disables snap. Ctrl + wheel zooms.
- **Keys:**

| Key | Action |
|---|---|
| Space | Play/pause |
| ←/→, Shift+←/→ | Seek 0.1 s / one frame |
| C | Add cue at playhead |
| M | Drop marker at playhead (also while playing) |
| Delete | Delete selected cues and markers |
| Ctrl+D | Duplicate |
| Ctrl+C / Ctrl+V | Copy / paste at playhead (works across profiles) |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo |
| `[` / `]` / `\` | Loop start / loop end / clear loop |
| F / Shift+F | Zoom to fit / zoom to selection |
| + / − | Zoom |

- **Cue panel:** label and custom block color; numeric start/duration with ±1 frame and ±0.1 s
  buttons; searchable effect and palette picker with favorites and recents. When the board reports
  WLED fxdata, only the sliders the effect uses are shown, with their real names. Every panel edit is
  undoable; a burst of edits to one cue (slider drag, typing) is one undo step.
- **Markers:** lane above the waveform. Drag to move, double-click to rename. Select markers, then
  "Markers → cues" makes one cue per marker from a template. Saved with the profile and in exports.
- **Onsets:** ticks on the waveform from audio analysis. The threshold slider filters them.
  "Generate cues…" makes one cue per onset above the threshold in a range (default: loop A–B).
- **Loop A–B:** preview playback loops the range; audition follows.
- **Time editing:** Δ s field, then "⇤ Shift"/"Shift ⇥" moves everything after the playhead (or only
  the selection), "Insert time" opens a gap at the playhead, "Delete A–B" removes the loop range and
  closes the gap.
- **Paste remap:** when pasted cues target boards missing in this profile, a dialog maps each source
  board to a lane here or drops it.
- **Templates…:** parametric generators (heartbeat, breathe, fire, police, strobe burst, creep,
  lightning) with a parameter form and a live preview on the ribbon. "Save as template" stores the
  selection (relative times) on the hub; saved templates appear in the same dialog.
- **Mute/solo (M/S in the lane header):** muted lanes get the profile base during live audition.
- **WLED segments:** boards with more than one segment get ▸ in the lane header to show one
  sub-lane per segment. Cues added on a sub-lane target only that segment; the cue panel target list
  has per-segment checkboxes. Segment cues on a collapsed board are drawn on the board lane.
  Segments follow on/brightness of the whole board; effect, palette, speed, intensity and colors are per segment.
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
- **Failure fallbacks:** frozen or crashed video is restarted and resumed once, then the show ends
  in idle.
  "Save fallback" writes the board's base look as WLED preset 250 and sets it as boot preset, so a
  power-cycled board boots into it without the hub. Manual, since it writes flash. Needs an active
  profile base or a manual state, and refuses during a show, blackout or setup action.
- **Test pattern:** alignment grid on projector. Stops any show; off ⇒ back to active profile idle.
  Blackout turns it off and it cannot be turned on during blackout.
- **Backups:** zip of DB + config, no media. Restore stages DB and restarts. Restored config goes to
  `/var/lib/spookhub/config.restored.toml` (since `/etc` is read-only for the service), copy by hand.
