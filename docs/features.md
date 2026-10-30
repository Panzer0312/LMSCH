# Features

## Pages

| Route | Page | Does |
|---|---|---|
| `/` | Dashboard (phone-first) | Preflight banner, active profile + progress, scene card (scene active), Trigger, Play/Stop, Next, master brightness, board health strip, last 20 events, History, links to `/show` and `/moments` |
| `/profiles[/:id]` | Profiles | List, create, edit, duplicate, delete, activate. Light, video or scene type, board selection or "All boards", base state + per-board overrides, playback mode, idle video, cooldown, ambience, fade, idle loop length, scene editor, cue list form |
| `/profiles/:id/timeline` | Timeline editor | See below. Desktop-first, read-only on phone |
| `/boards` | Boards | Discovered vs adopted, status, RSSI, latency, firmware, LEDs, live state. Rename, identify, on/off, brightness, sync offset (ms), "Save fallback" per board and for all, open WLED UI, refresh, remove, scan, add by IP, "Check settings", new-board guide |
| `/setup` | Setup | Five steps with status dots, see below. Phone-friendly |
| `/show` | Show mode | Full-screen big buttons for the night, soundboard, Moments link, see below |
| `/moments` | Moments | Button board for one-shot moments, see below. Phone and tablet |
| `/media` | Media | Video and audio upload with progress, All/Video/Audio filter, probe info, transcode, used-by, delete (blocked if used), aspect badge, black check, per-video display profile |
| `/video` | Video setup | Setup mode, source on projector, display profile editor (frame, keystone, mask, image), A/B compare. Works on a phone |
| `/settings` | Settings | Sync offset, health interval, default transition, log level, mpv output, brightness and volume caps, show mode toggle, test pattern, PIN, trigger token, backup/restore. Footer shows deployed commit and install time |

Blackout button is in the header on every page. Stops video, blacks projector, boards off, held until released.

## Setup page

| Step | Does |
|---|---|
| Projector output | DRM connector (connected first) and mode dropdowns. Applying restarts mpv (screen black ~1 s). Test pattern button. Alignment pattern picker (enters setup mode, saved geometry applied; empty choice or leaving the page leaves it; disabled while a show plays) and "Open video setup" link |
| Audio output | Device dropdown (friendly names), volume, audio delay, "Play test sound", reload devices |
| Boards | Online/adopted count, "Check all settings", link to Boards |
| Trigger devices | Token, curl and ESPHome snippets with hub URL, live "waiting for trigger" light, "Send test trigger" |
| Sync calibration | Loops a clip on the projector; boards flash white on the beat. Global offset and per-board offset sliders apply live. Move until flashes match |

The page also lists the preflight checks. "Test everything" flashes all boards white 2 s, plays the
test sound, shows the pattern 2 s, then restores idle. Setup actions are disabled while a show plays
or show mode is on.

## Video setup

- **Setup mode:** "Start setup mode" stops any show. "Leave setup mode" (or leaving the page) restores
  the active profile idle state. Refused during blackout and show lock. Unsaved live edits are
  dropped on leave; the projector falls back to the saved geometry.
- **Source on projector:** an alignment pattern, any media file (with a scrub slider) or the active
  profile idle video. Picking a source enters setup mode.
- **Control groups**, each with Reset:

| Group | Controls |
|---|---|
| Frame | Zoom, pan, stretch, align, rotate 0/90/180/270, aspect override, crop, flip H (rear) / flip V (ceiling) |
| Keystone | Corners 1–4 (TL, TR, BR, BL), X/Y each. Drag the numbered handles in the preview |
| Mask | Top, right, bottom, left, feather |
| Image | Brightness, contrast, gamma, saturation |

- **Nudge:** in a number field or slider, arrow keys step, Shift + arrow steps fine.
- **Preview:** browser mimic of the geometry; orange = output bounds. The projector is the truth.
  On a phone the preview folds away with "Hide preview".
- **Profile bar:** pick a display profile (or none = identity), Save, Save as…, Rename, Delete,
  Set as default (global), "Use for this video" when the source is media and a profile is picked in the bar (never pins the inherited one). "unsaved" badge while dirty.
- **Warp warning:** when the geometry uses corners, mask or flip and mpv's `vo` is not `gpu-next`, a banner says they have no effect.
- **A/B:** "A saved" / "B edited" flips the projector between saved and edited geometry.
- **Live apply:** edits reach mpv within ~30 ms while setup mode is on. Without setup mode, edits
  and saves work but do not show.

**Precedence:** show profile's display profile > video's display profile > global default > identity.
A deleted profile falls through to the next.

| Pattern | Use |
|---|---|
| Grid | Keystone, straightness, centre |
| Numbered corners + border | Match corners 1–4 to the keystone handles |
| Crosshair + circles | Aspect: circles must be round |
| Edge chase (moving) | Edges inside the surface; set mask |
| Scan line H / V (moving) | Find clipped or hidden edges |
| Focus (star + line pairs) | Lens focus |
| Grey ramp | Brightness/gamma so black is invisible on the scrim |
| Checkerboard, white, black | Hot spots, light leaks, projector black level |
| Safe area (mask check) | Mid grey; shows the masked usable area |

Other pages:

- **Media audio:** audio files (mp3, wav, ogg, flac, m4a, opus) get a waveform and onsets, no
  thumbnail, transcode or black check. Used-by also lists profiles using it in sound cues or ambience.
- **Media:** badge "aspect … differs from display" when a video's aspect is more than 0.02 off
  the default. "Create display profile for this video" copies the default with that aspect and assigns
  it. "Black check" samples frames and warns when the background is not near black.
- **Profiles editor:** "Display profile" select sets a per-show profile (top precedence).

## Preflight

Twelve checks, each ok, warn or fail, with a fix link: boards online, latency (< 100 ms), WiFi sleep,
mpv, keystone/mask (warn when the geometry needs the warp shader but `vo` is not `gpu-next`),
audio device, active profile media, disk (> 2 GB), clock sync, mpv restarts last hour, recent
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
- **Soundboard** (on `/show`, shown when audio media exists, collapsible): one button per audio
  file, "Sound volume" slider (0–130 %), "Stop sounds". One sound at a time on the effects player;
  a new press replaces the last. Works under the lock, refused in blackout.
- **🔔 Moments** button opens `/moments`. Also in the nav.
- **Caps:** `brightness_cap` and `volume_cap` limit the master sliders everywhere, effects included.

## Moments (`/moments`)

- **Buttons:** grid, 2 columns at 375 px, 4 on tablet, 6 on desktop. Icon (emoji), name, color.
  Press plays one variant once, then returns to what ran before.
- **Rings:** green progress ring while playing; orange cooldown ring with seconds left after.
  Pressing during cooldown shows "cooldown N s" and does nothing. Refusals (blackout, setup,
  busy) show as a short message on the button.
- **Top bar:** Stop (ends the moment, restores), Blackout, Edit / Done, Kiosk.
- **Kiosk:** full-screen overlay without nav (browser fullscreen where supported). Edit is hidden.
- **Edit mode** (hidden under the show lock and in kiosk): per button ↑/↓ reorder, Edit, Del;
  "+ Add moment".
- **Editor:** icon, name, color, cooldown (s), when busy (Ignore / Interrupt / Queue), order
  (Shuffle / Weighted). Variants: video profile, light profile, sound (volume, fade in/out ms),
  ambience (Keep / Mute / Replace + replacement sound), weight, "Test", "Remove". Test plays that
  variant now, without cooldown.
- **Variant pick:** shuffle never repeats the last variant; weighted picks by weight, no immediate
  repeat.
- **Busy:** something playing (moment, show, scene item). Ignore refuses, Queue plays it after
  (one queued), Interrupt ends it and starts.
- **Restore:** idle video resumes where it was, idle lights and ambience carry on, boards the
  moment touched go back to the profile look. Cooldown starts when the moment ends.

## History

Dashboard "History": triggers tonight (since local noon), triggers per hour bars, last 100 events
from the persistent log. Events survive restarts and are kept 7 days.

## Profiles and playback

- **Light profile:** applies base states to its boards. Optional "Idle loop length (s)": its idle
  cue track loops on the boards (breathing lights); edit it in the timeline Idle tab.
- **Video profile:** video + cues + mode `once`, `loop` or `idle_trigger` (idle video or black between
  triggered plays, with cooldown). End or stop ⇒ boards to base, idle video or black.
- **Scene profile:** a playlist of light and video profiles. Editor: add items (each once),
  weight, ↑/↓ order, remove; order Sequential / Shuffle / Weighted; idle profile (video or light,
  runs between items); light hold (s) for light items; "Play back to back, no trigger" (chain);
  preview "Next 10 picks (example)". Trigger plays the next item once, then returns to the idle
  part where it was. Chain stops after a manual stop or blackout until the next trigger.
- **Dashboard scene card:** scene name, "Next: …" (with "pinned"), Skip (pick another next),
  "Pin next" select, Upcoming (10), Recently played (5).
- **Ambience** (any type): "Ambient sound loop" with sound, volume and "Duck to during scare" (default 30 %). Loops while the
  profile is active, ducks while the main video plays, returns after.
- **Fade idle → scare (ms)** (video and scene, default 400, 0 = hard cut): picture and sound fade out, the
  scare loads, fades in.
- **Sound cues** play from the timeline Sound lane, see [`timeline.md`](timeline.md).
- One profile active at a time. Switch stops video and applies new base states.

## Timeline editor

See [`timeline.md`](timeline.md).

## Operations

- **Recommended settings:** reads `/json/cfg`, offers confirmed one-click fix. Check: WiFi sleep off.
- **Transcode** to H.264 1080p for non-H.264/HEVC or >1080p. Media id kept, profiles keep working.
- **Export/import** profile JSON with cues. Video and ambient sound matched by filename (video can be picked on import), scene items by profile name. Unknown board targets and missing scene items dropped.
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
