# Operations

## Deploy to mini PC

**Current workflow (in use):** clone the repo on the hub once, then for every update:

```bash
cd ~/spookhub && git pull && sudo scripts/install.sh
```

`scripts/install.sh` is idempotent: apt packages (mpv, ffmpeg, avahi, alsa-utils, VA-API driver),
`spookhub` user (groups video, render, audio), frontend build if inputs changed, copies the repo to `/opt/spookhub`, venv,
`/etc/spookhub/config.toml` (kept if present), systemd unit, console blanking off, sleep targets
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

## Troubleshooting

| Symptom | Check |
|---|---|
| Black screen | `journalctl -u spookhub -e` for mpv errors. `vainfo`. Try `extra_args = ["--drm-connector=HDMI-A-1"]` or `hwdec = "no"`. |
| "mpv exited, restarting" loop | Another process holds DRM (desktop or KMS getty). User in `video` + `render`? |
| No sound | `aplay -l`, then `extra_args = ["--audio-device=alsa/hdmi:CARD=PCH,DEV=0"]` or analog device. |
| Board offline | Ping. Router lease. Boards → Scan network or add by IP. |
| Lights lag video | Disable WiFi sleep. Run sync calibration on Setup, adjust global and per-board offset. |
| Wrong or no projector output | Setup → Projector output: pick connector and mode. `scripts/hubcheck.sh` lists connectors. |
| Wrong or no audio device | Setup → Audio output: pick device, "Play test sound". A rejected device is reverted and logged. |
| Service restarts every 30 s | Watchdog firing: event loop hung. `journalctl -u spookhub` before the restart. |
| Edits return 423 | Show mode is on. Unlock on `/show` or Settings (PIN if set). |
| Preflight red | Click the failing check; the fix link points to the page. |

## Known untested on real hardware

Phase 1 features are built and tested against the simulator only: DRM connector/master selection,
ALSA device choice, test sound and calibration clip on real output, stall recovery, WLED
`/json/cfg` key `wifi.sleep` on installed firmware, fallback preset on real boards, systemd
watchdog, `install.sh` and `deploy.sh` end to end. Covered by roadmap phase 1, task group 1.1.
