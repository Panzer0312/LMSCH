# Operations

## Deploy to mini PC

**Current workflow (in use):** clone the repo on the hub once, then for every update:

```bash
cd ~/spookhub && git pull && sudo scripts/install.sh
```

`scripts/install.sh` is idempotent: apt packages (list: README "Debian 13 hub: required packages"),
`spookhub` user (groups video, render, audio), frontend build if inputs changed, copies the repo to `/opt/spookhub`, venv,
`/etc/spookhub/config.toml` (kept if present), ALSA dmix (`/etc/asound.conf`, see below), jukebox
speaker (raspotify) and port-80 forward (see Jukebox), systemd unit, console blanking off, sleep targets
masked, restarts the service, prints the admin URL and the guest URL `http://<ip>/music`. Reboot once
after the first run so `consoleblank=0` applies. The jukebox steps are optional: a failure prints a
`WARNING:` and the install continues.

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
- Content: per playback port of `aplay -l` a dmix `spookmix_C_D` (`ipc_key 5200+100·C+D`,
  `ipc_gid audio`, `ipc_perm 0660`, 48 kHz on `hw:C,D`) and a plug `spook_C_D` with an ALSA hint, so
  Setup → Audio output lists it as "Spookhub shared: HDMI 0 · <display> (card, hw:C,D)".
  `pcm.!default` = plug → dmix of the detected port, `ctl.!default` on that card.
- Device choice: `SPOOKHUB_ALSA_CARD` / `SPOOKHUB_ALSA_DEVICE` env when set
  (`sudo SPOOKHUB_ALSA_CARD=1 SPOOKHUB_ALSA_DEVICE=3 scripts/install.sh`). Else the first HDMI
  port with a connected display (aplay shows its name instead of `[HDMI N]`), else the first HDMI
  port, else `hw:0,0` with a warning.
- Pick a "Spookhub shared" output (`alsa/spook_C_D`) or Default in Setup → Audio output. A raw
  `alsa/hdmi:…` or `alsa/hw:…` device grabs the port and blocks effects, ambience and the
  jukebox; Setup and preflight warn. Re-run `install.sh` after plugging in a new display so its
  name shows.
- Dev desktop (PipeWire) needs nothing.

## Jukebox (Spotify)

Guests open `http://<hub-ip>/music`, search Spotify and queue songs. The hub plays them through
librespot (package `raspotify`), a Spotify Connect speaker. Music plays on Setup → Audio output,
like video and effects.

**What `install.sh` does** (idempotent):

| Step | Detail | Opt-out |
|---|---|---|
| raspotify | Adds apt repo `https://dtcooper.github.io/raspotify` (key `/usr/share/keyrings/raspotify_key.asc`), installs `raspotify`. | `SPOOKHUB_SPOTIFY=0` |
| `/etc/raspotify/conf` | Written when absent or marked `# spookhub-managed` (else kept). Name = `[jukebox] device_name` (default `Spookhub`), backend `subprocess` = `/opt/spookhub/scripts/music-sink.sh` (aplay on the device in `/var/lib/spookhub-audio/device`, written by the hub; dir 0755 owned by `spookhub`), 320 kbit/s, initial volume 70, normalisation on, audio cache off, credential cache on. | delete the marker line |
| Unit drop-in | `raspotify.service.d/spookhub.conf`: `SupplementaryGroups=audio` (dmix `ipc_gid audio`), `CacheDirectory=raspotify` (cached login). Enables raspotify; restarts it only when conf or drop-in changed or it is not running, so a re-deploy mid-party keeps the music. Network steps time out (curl 60 s, apt 300 s). | |
| Port 80 | `spookhub-http.socket` (`ListenStream=80`) + `spookhub-http.service` (`systemd-socket-proxyd 127.0.0.1:<port>`, `DynamicUser=yes`). Port = `SPOOKHUB_SERVER__PORT` from the spookhub unit or a drop-in, else `[server] port`, else 8080. Proxy restarts only when its unit changed. Skipped with a warning when another program listens on :80; guests then use `http://<ip>:<port>/music`. | `SPOOKHUB_PORT80=0` (also disables an existing forward) |

Example: `sudo SPOOKHUB_SPOTIFY=0 SPOOKHUB_PORT80=0 scripts/install.sh`.

**One-time host setup:**

1. Spotify Premium account (Web API playback control needs it).
2. Open `/music` on the host's PC first: the first device to join becomes admin. Lost admin:
   Settings → "Reset jukebox admin".
3. Create an app at [developer.spotify.com](https://developer.spotify.com/dashboard) (Web API).
   Add the redirect URI shown on the **Host** tab (default
   `http://127.0.0.1:<port>/api/jukebox/spotify/callback`; override `[jukebox] redirect_uri`).
4. Paste the app's Client ID on the Host tab, press **Connect**, log in. If the browser is not on
   the hub, the loopback redirect fails to load: paste that address bar URL into the Host tab.
5. On the same WiFi, open a Spotify app, pick the device **Spookhub** once. librespot caches the
   login; only then does it appear in the Web API device list.
6. Print or show the QR code from the Host tab.

Dev-mode Spotify apps only accept accounts listed under the app's **User Management**: add the host
account there.

Proxied guests reach the app from `127.0.0.1`. Diagnostics: `scripts/hubcheck.sh` (section
jukebox) shows raspotify state, the socket state and the :80 listener.

## Diagnostics and watchdog

- `scripts/hubcheck.sh` is read-only, needs no root. It prints mpv version, `vainfo` summary, DRM
  connectors and status, `aplay -l`, free disk, time sync, service state, raspotify and port-80
  forward state. Paste it into bug reports.
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
| Jukebox: songs queued, "Silence in the crypt…" | The card names the reason. Speaker not found ⇒ row below. "Spotify is playing on X" ⇒ stop it there. Host **▶ Play** starts the queue by hand and shows the error as a toast. |
| Jukebox: device not found | `systemctl status raspotify`. Select "Spookhub" once in a Spotify app on the same WiFi. Name must match `[jukebox] device_name` (re-run `install.sh` after changing it). |
| Jukebox: skips through songs, "Speaker … stopped playing" | The sink can't open the output (raw device held by mpv). Setup → Audio output: pick "Spookhub shared: …". `scripts/hubcheck.sh` jukebox section shows the music output and the last raspotify errors. |
| Jukebox: no sound from Spotify, mpv works | `journalctl -u raspotify -e` (ALSA open errors). `/etc/raspotify/conf` has `LIBRESPOT_BACKEND=subprocess` and the `music-sink.sh` device (re-run `install.sh`)? `/var/lib/spookhub-audio/device` names the Setup output? Drop-in `raspotify.service.d/spookhub.conf` present (group `audio` for dmix)? `/etc/asound.conf` is dmix with `ipc_gid audio`. |
| Jukebox: Spotify 403 | Account is not Premium, or the Spotify app is in development mode and the account is missing from its User Management. |
| Guests can't open `http://<ip>/music` | `systemctl status spookhub-http.socket`. Installer warned about another :80 listener? Use `http://<ip>:<port>/music`. |
| Keystone/mask has no effect | mpv `vo` is not `gpu-next`. Set it in Settings → "mpv video output (vo)" or `[mpv] vo` in config. |

## Known untested on real hardware

Phase 1 features are built and tested against the simulator only: DRM connector/master selection,
ALSA device choice, test sound and calibration clip on real output, stall recovery, WLED
`/json/cfg` key `wifi.sleep` on installed firmware, fallback preset on real boards, systemd
watchdog, `install.sh` and `deploy.sh` end to end. Covered by roadmap phase 1, task group 1.1.

Phase 4 is simulator-only too. Open on the hub: dmix on HDMI with three mpv openers, sound cue
latency (±50 ms of its timestamp) with main audio unaffected, smoothness of the IPC
brightness/volume fade.

Jukebox is untested on the hub too: raspotify on trixie amd64, librespot through dmix next to
three mpv openers, the port-80 forward.
