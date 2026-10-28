# Operations

## Deploy to mini PC

1. Debian 13 minimal with SSH, a sudo user.
2. First time: `scripts/deploy.sh user@minipc --install`. Builds frontend, rsyncs, runs
   `scripts/install.sh` (idempotent): apt packages (mpv, ffmpeg, avahi, alsa-utils, VA-API driver),
   `spookhub` user (groups video, render, audio), venv, `/etc/spookhub/config.toml`, systemd unit,
   console blanking off, sleep targets masked. Prints URL.
3. Updates: `scripts/deploy.sh user@minipc` (sync, reinstall, restart).
4. Reboot once so `consoleblank=0` applies.

| What | Where |
|---|---|
| Code | `/opt/spookhub` |
| Config | `/etc/spookhub/config.toml` (example: `deploy/config.example.toml`) |
| Data (DB, media, derived) | `/var/lib/spookhub` |
| Logs | `journalctl -u spookhub -f` |

Every config key has an env override, e.g. `SPOOKHUB_MPV__EXTRA_ARGS='["--drm-connector=HDMI-A-1"]'`.

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
| Lights lag video | Disable WiFi sleep. Raise global sync offset in Settings. |

## Known untested on real hardware

DRM connector/master selection, ALSA device choice, WLED `/json/cfg` key `wifi.sleep` on installed
firmware, `install.sh` and `deploy.sh` end to end. Covered by roadmap phase 1, task group 1.
