# Operations

## Deploy to mini PC

**Current workflow (in use):** clone the repo on the hub once, then for every update:

```bash
cd ~/spookhub && git pull && sudo scripts/install.sh
```

`scripts/install.sh` is idempotent: apt packages (list: README "Debian 13 hub: required packages"),
`spookhub` user (groups video, render, audio), frontend build if inputs changed, copies the repo to `/opt/spookhub`, venv,
`/etc/spookhub/config.toml` (kept if present), ALSA dmix (`/etc/asound.conf`, see below), systemd unit, console blanking off, sleep targets
masked, restarts the service, prints the URL. Reboot once after the first run so `consoleblank=0` applies.

The installer installs `nodejs npm git` and rebuilds `frontend/dist` whenever a hash of the frontend
inputs (`scripts/frontend-hash.sh`) differs from `frontend/dist/.build-hash`. npm and git run as the
user who called sudo. It writes `/opt/spookhub/version.json` with the deployed commit.

**Alternative:** `scripts/deploy.sh user@minipc [--install]` builds the frontend on the dev
machine, rsyncs and runs the installer remotely.

| What | Where |
|---|---|
| Code | `/opt/spookhub` |
| Config | `/etc/spookhub/config.toml` (example: `deploy/config.example.toml`) |
| Data (DB, media, derived) | `/var/lib/spookhub` |
| Logs | `journalctl -u spookhub -f` |
| Diagnostics | `scripts/hubcheck.sh` |

Every config key has an env override, e.g. `SPOOKHUB_MPV__EXTRA_ARGS='["--drm-connector=HDMI-A-1"]'`.

## Audio mixing (ALSA dmix)

Main video, sound effects (`fx`) and ambience are three mpv processes on one card. A plain hw
device allows one opener; `dmix` lets them share it.

- `install.sh` writes `/etc/asound.conf` when it is absent or carries the `# spookhub-managed`
  marker (older installs: the `# Written by Spookhub install.sh` header). Any other file is kept;
  it must define a dmix default itself. Delete the marker line to stop rewrites.
- Content: `pcm.!default` = plug → `spookmix` (dmix, `ipc_gid audio`, `ipc_perm 0660`, 48 kHz) on
  `hw:CARD,DEVICE`, `ctl.!default` on that card.
- Device choice: `SPOOKHUB_ALSA_CARD` / `SPOOKHUB_ALSA_DEVICE` env when set
  (`sudo SPOOKHUB_ALSA_CARD=1 SPOOKHUB_ALSA_DEVICE=3 scripts/install.sh`). Else the first HDMI
  port with a connected display (aplay shows its name instead of `[HDMI N]`), else the first HDMI
  port, else `hw:0,0` with a warning.
- mpv audio device must be `auto` or `alsa/default` (Setup → Audio output or `mpv_audio_device`).
  A raw `alsa/hdmi:…` or `alsa/hw:…` device grabs the card and blocks the effects players.
- Dev desktop (PipeWire) needs nothing.

## Diagnostics and watchdog

- `scripts/hubcheck.sh` is read-only, needs no root. It prints mpv version, `vainfo` summary, DRM
  connectors and status, `aplay -l`, free disk and time sync. Paste it into bug reports.
- The unit is `Type=notify` with `NotifyAccess=main`, `WatchdogSec=30` and `TimeoutStartSec=60`.
  The hub pings systemd every 15 s from its event loop. No ping for 30 s ⇒ systemd restarts it.
  `Restart=always` covers crashes.
- Boards keep a local fallback look (WLED preset 250) once saved from Boards. See features.

## Hardware tips

- **Video:** pure black backgrounds; black emits no light, only the ghost shows on scrim or window.
- **Surface:** grey scrim, voile curtain, or window with rear-projection film. Projector hidden behind.
- **Static IPs:** DHCP reservation per board. Spookhub follows IP changes by MAC, reservation avoids gaps.
- **WiFi sleep:** disable on every board (WLED Config → WiFi Setup). Otherwise latency jumps to 100s of ms.
- **Codecs:** H.264 or HEVC ≤1080p decode in hardware on Intel mini PCs.
- **New boards:** flash via [install.wled.me](https://install.wled.me), join `WLED-AP`, set WLAN at
  4.3.2.1. Board appears under Discovered.

## Projector setup

1. Optics first: position the projector, use its lens shift, optical keystone and zoom. Show
   **Focus** and set the lens. Digital keystone blurs.
2. Open `/video`, start setup mode with **Grid** or **Numbered corners**. Drag corners 1–4 (TL, TR,
   BR, BL) until the border sits on the surface edges.
3. **Crosshair + circles:** set aspect/stretch until circles are round.
4. **Edge chase:** set the mask so nothing spills past the surface. Check with **Safe area**.
5. **Grey ramp:** lower brightness/gamma until black is invisible on the scrim.
6. Save, then "Set as default". Videos with other framing get their own profile (Media page).

Keystone, mask and flip run as an mpv shader and need `vo = "gpu-next"` (Settings → "mpv video output (vo)",
or `[mpv] vo` in config). Plain `gpu` ignores the shader; preflight warns "Keystone/mask need vo=gpu-next".

## Troubleshooting

| Symptom | Check |
|---|---|
| `install.sh` stops after a step header | It prints `install.sh failed at line N: <command>`. Fix that step, then re-run (the script is idempotent). |
| Black screen | `journalctl -u spookhub -e` for mpv errors. `vainfo`. Try `extra_args = ["--drm-connector=HDMI-A-1"]` or `hwdec = "no"`. |
| "mpv exited, restarting" loop | Another process holds DRM (desktop or KMS getty). User in `video` + `render`? |
| No sound | `aplay -l`. Check `/etc/asound.conf` points at the right `hw:CARD,DEVICE`; re-run `install.sh` with `SPOOKHUB_ALSA_CARD`/`SPOOKHUB_ALSA_DEVICE`. Keep the mpv device `auto` or `alsa/default`. |
| Video has sound, no effects/ambience | Main mpv holds the card raw: set the audio device to `auto`/`alsa/default`. `/etc/asound.conf` missing or not dmix (user file kept). `spookhub` user in group `audio`? `journalctl -u spookhub | grep -i 'fx\|ambient'`. |
| Board offline | Ping. Router lease. Boards → Scan network or add by IP. |
| Lights lag video | Disable WiFi sleep. Run sync calibration on Setup, adjust global and per-board offset. |
| Wrong or no projector output | Setup → Projector output: pick connector and mode. `scripts/hubcheck.sh` lists connectors. |
| Wrong or no audio device | Setup → Audio output: pick device, "Play test sound". A rejected device is reverted and logged. |
| Service restarts every 30 s | Watchdog firing: event loop hung. `journalctl -u spookhub` before the restart. |
| Edits return 423 | Show mode is on. Unlock on `/show` or Settings (PIN if set). |
| Preflight red | Click the failing check; the fix link points to the page. |
| Keystone/mask has no effect | mpv `vo` is not `gpu-next`. Set it in Settings → "mpv video output (vo)" or `[mpv] vo` in config. |

## Known untested on real hardware

Phase 1 features are built and tested against the simulator only: DRM connector/master selection,
ALSA device choice, test sound and calibration clip on real output, stall recovery, WLED
`/json/cfg` key `wifi.sleep` on installed firmware, fallback preset on real boards, systemd
watchdog, `install.sh` and `deploy.sh` end to end. Covered by roadmap phase 1, task group 1.1.

Phase 4 is simulator-only too. Open on the hub: dmix on HDMI with three mpv openers, sound cue
latency (±50 ms of its timestamp) with main audio unaffected, smoothness of the IPC
brightness/volume fade.
