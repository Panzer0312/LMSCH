#!/usr/bin/env bash
# Spookhub installer for a fresh Debian 13 (trixie) minimal system. Idempotent: safe to re-run.
# Usage (as root, from the project directory): sudo ./scripts/install.sh
set -euo pipefail
# set -e alone exits silently; say where it stopped.
trap 'echo "install.sh failed at line $LINENO (exit $?): $BASH_COMMAND" >&2' ERR

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/opt/spookhub
ETC=/etc/spookhub
DATA=/var/lib/spookhub
USER_NAME=spookhub

[[ $EUID -eq 0 ]] || { echo "run as root (sudo $0)"; exit 1; }
log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
warn() { echo "WARNING: $*" >&2; }

log "Installing packages"
export DEBIAN_FRONTEND=noninteractive
# A dead optional repo (raspotify) must not block re-runs; a real problem fails at apt-get install.
apt-get update -qq || warn "apt-get update reported errors (see above); continuing with cached lists"
apt-get install -y -qq --no-install-recommends \
  mpv ffmpeg python3 python3-venv python3-pip avahi-daemon libnss-mdns alsa-utils \
  vainfo mesa-va-drivers libgl1-mesa-dri libegl1 fonts-dejavu-core \
  nodejs npm git ca-certificates rsync curl >/dev/null
# Intel VA-API: iHD for Broadwell+ (non-free), i965 for older chips. Install whatever exists.
if grep -qi intel /proc/cpuinfo; then
  apt-get install -y -qq intel-media-va-driver >/dev/null 2>&1 \
    || apt-get install -y -qq intel-media-va-driver-non-free >/dev/null 2>&1 || true
  apt-get install -y -qq i965-va-driver >/dev/null 2>&1 || true
fi

log "Creating user and directories"
id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$USER_NAME"
for g in video render audio; do getent group "$g" >/dev/null && usermod -aG "$g" "$USER_NAME"; done
install -d -m 0755 "$APP" "$ETC"
install -d -m 0750 -o "$USER_NAME" -g "$USER_NAME" "$DATA" "$DATA/media"
# The hub writes its audio output here for the jukebox sink, which runs as raspotify's dynamic user.
install -d -m 0755 -o "$USER_NAME" -g "$USER_NAME" /var/lib/spookhub-audio

log "Frontend"
# frontend/dist is not in git. Rebuild whenever its inputs changed (git pull workflow), not only when
# it is missing. Build as the user who ran sudo so node_modules in their checkout stay theirs.
AS=()   # run as the invoking user: their checkout, their node_modules, git ownership check passes
[[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]] && AS=(sudo -u "$SUDO_USER" -H)
WANT_HASH="$("$SRC/scripts/frontend-hash.sh")"
HAVE_HASH="$(cat "$SRC/frontend/dist/.build-hash" 2>/dev/null || true)"
if [[ ! -f "$SRC/frontend/dist/index.html" || "$WANT_HASH" != "$HAVE_HASH" ]]; then
  echo "building frontend ($WANT_HASH)"
  (cd "$SRC/frontend" && "${AS[@]}" npm ci --no-audit --no-fund --loglevel=error && "${AS[@]}" npm run build)
  echo "$WANT_HASH" | "${AS[@]}" tee "$SRC/frontend/dist/.build-hash" >/dev/null
else
  echo "frontend up to date ($WANT_HASH)"
fi

log "Copying application to $APP"
rsync -a --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude 'frontend/src' --exclude '/venv' \
  "$SRC/" "$APP/"

# Version stamp shown in the UI so you can see which commit runs on the hub.
COMMIT="$("${AS[@]}" git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo unknown)"
"${AS[@]}" git -C "$SRC" diff --quiet HEAD 2>/dev/null || COMMIT="$COMMIT-dirty"
printf '{"commit": "%s", "installed_at": "%s"}\n' "$COMMIT" "$(date -Iseconds)" > "$APP/version.json"

log "Python virtualenv"
[[ -x "$APP/venv/bin/python" ]] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q "$APP/backend"

log "Config"
if [[ ! -f "$ETC/config.toml" ]]; then
  install -m 0644 "$APP/deploy/config.example.toml" "$ETC/config.toml"
else
  echo "keeping existing $ETC/config.toml"
fi
# Reads one key of one [section] from the config; prints nothing when absent.
config_value() {
  awk -v sec="[$1]" -v key="$2" '
    /^[[:space:]]*\[/ { s = ($1 == sec); next }
    s && $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      sub(/^[^=]*=[[:space:]]*/, ""); sub(/[[:space:]]*#.*$/, ""); gsub(/"/, ""); print; exit
    }' "$ETC/config.toml" 2>/dev/null || true
}
# The port the app really listens on, which the port-80 forward targets: SPOOKHUB_SERVER__PORT set
# in the unit or a drop-in (it overrides the config in the app), else [server] port, else 8080.
unit_port() {
  cat "$APP/deploy/spookhub.service" /etc/systemd/system/spookhub.service.d/*.conf 2>/dev/null \
    | sed -nE 's/^[[:space:]]*Environment=.*SPOOKHUB_SERVER__PORT=([0-9]+).*/\1/p' | tail -n 1
}
PORT="$(unit_port || true)"
[[ -n "$PORT" ]] || PORT="$(config_value server port)"
[[ "$PORT" =~ ^[0-9]+$ ]] || PORT=8080

log "ALSA mixing"
# Main video, sound effects and ambience are three mpv processes on one card: plain hw devices
# allow a single opener, dmix lets them share it. A user-written file is never touched; a file
# carrying the spookhub-managed marker is rewritten so improved detection reaches old installs.
# Card/device: SPOOKHUB_ALSA_CARD / SPOOKHUB_ALSA_DEVICE, else first HDMI playback device, else 0,0.
# The "Written by" header also counts: files from earlier installs had no marker but ipc_perm 0666.
if [[ ! -e /etc/asound.conf ]] \
  || grep -qE '^# (spookhub-managed|Written by Spookhub install\.sh)' /etc/asound.conf; then
  if [[ -n "${SPOOKHUB_ALSA_CARD:-}" ]]; then
    ALSA_CARD="$SPOOKHUB_ALSA_CARD"
    ALSA_DEVICE="${SPOOKHUB_ALSA_DEVICE:-0}"
  else
    # LC_ALL=C: aplay localizes "card"/"device". A connected display shows its own name (ELD) in
    # the trailing brackets, an empty port shows "[HDMI N]", so prefer the first connected port.
    APLAY="$(LC_ALL=C aplay -l 2>/dev/null || true)"
    pick_hdmi() { sed -nE "/^card [0-9]+:.*device [0-9]+:.*HDMI/{$1s/^card ([0-9]+):.*device ([0-9]+):.*/\\1 \\2/p;q}" <<<"$APLAY"; }
    HDMI="$(pick_hdmi '/\[HDMI [0-9]+\]$/b;' || true)"
    [[ -n "$HDMI" ]] || HDMI="$(pick_hdmi '' || true)"
    if [[ -n "$HDMI" ]]; then
      read -r ALSA_CARD ALSA_DEVICE <<<"$HDMI"
      ALSA_DEVICE="${SPOOKHUB_ALSA_DEVICE:-$ALSA_DEVICE}"
      echo "detected HDMI playback device hw:${ALSA_CARD},${ALSA_DEVICE}"
    else
      ALSA_CARD=0
      ALSA_DEVICE="${SPOOKHUB_ALSA_DEVICE:-0}"
      echo "WARNING: no HDMI playback device found (aplay missing or no HDMI card); using hw:0,${ALSA_DEVICE}" >&2
    fi
  fi
  # One shared output (plug -> dmix) per playback port, listed in Setup → Audio output as
  # "Spookhub shared: …" (alsa/spook_C_D). Video, effects, ambience and the jukebox all share the
  # port picked there; the default points at the detected port.
  APLAY="${APLAY:-$(LC_ALL=C aplay -l 2>/dev/null || true)}"
  PORTS="$(sed -nE 's/^card ([0-9]+): [^ ]+ \[([^]]*)\], device ([0-9]+): ([^[]*)\[([^]]*)\].*/\1 \3 \2|\4|\5/p' <<<"$APLAY")"
  grep -q "^${ALSA_CARD} ${ALSA_DEVICE} " <<<"$PORTS" || PORTS="${ALSA_CARD} ${ALSA_DEVICE} card ${ALSA_CARD}|device ${ALSA_DEVICE}|
${PORTS}"
  {
    cat <<CONF
# spookhub-managed
# Written by Spookhub install.sh (dmix per port so several players share it).
# Remove the marker line above to keep install.sh from rewriting this file.
pcm.!default {
  type plug
  slave.pcm "spookmix_${ALSA_CARD}_${ALSA_DEVICE}"
}
ctl.!default {
  type hw
  card ${ALSA_CARD}
}
CONF
    while read -r C D REST; do
      [[ -n "$C" ]] || continue
      IFS='|' read -r CARDNAME DEVNAME ELD <<<"$REST"
      DEVNAME="${DEVNAME% }"
      # An HDMI port with a display shows the display's name instead of "HDMI N".
      LABEL="$DEVNAME"
      [[ -n "$ELD" && "$ELD" != "$DEVNAME" && ! "$ELD" =~ ^HDMI\ [0-9]+$ ]] && LABEL="$DEVNAME · $ELD"
      LABEL="${LABEL//\"/} (${CARDNAME//\"/}, hw:$C,$D)"
      cat <<CONF
pcm.spookmix_${C}_${D} {
  type dmix
  ipc_key $((5200 + C * 100 + D))
  ipc_gid audio
  ipc_perm 0660
  slave {
    pcm "hw:${C},${D}"
    rate 48000
  }
}
pcm.spook_${C}_${D} {
  type plug
  slave.pcm "spookmix_${C}_${D}"
  hint {
    show on
    description "Spookhub shared: ${LABEL}"
  }
}
CONF
    done <<<"$PORTS"
  } >/etc/asound.conf
  echo "wrote /etc/asound.conf (dmix on hw:${ALSA_CARD},${ALSA_DEVICE})"
else
  echo "keeping existing /etc/asound.conf (not spookhub-managed): make sure it defines a dmix default"
fi
echo "note: pick a 'Spookhub shared: …' output (alsa/spook_C_D) or 'auto' in Setup → Audio output;"
echo "      raw 'alsa/hdmi:...' or 'alsa/hw:...' devices grab the port and block effects and music."

log "Jukebox: Spotify Connect speaker (raspotify)"
# Optional: any failure (no network, repo down, no package for this arch) only warns. Each step
# chains "|| return 1" because errexit is off inside a function called from an if.
RASPOTIFY_KEY=/usr/share/keyrings/raspotify_key.asc
# Writes stdin to $1 only when the content differs and sets CHANGED=1 then. Re-deploying mid-party
# must not restart raspotify or the proxy (music would drop) unless their config really changed.
CHANGED=0
put_file() {
  local tmp
  tmp="$(mktemp "$1.XXXXXX")" || return 1
  cat >"$tmp" || { rm -f "$tmp"; return 1; }
  if cmp -s "$tmp" "$1"; then
    rm -f "$tmp"
  else
    if ! { chmod 0644 "$tmp" && mv -f "$tmp" "$1"; }; then rm -f "$tmp"; return 1; fi
    CHANGED=1
  fi
}
install_raspotify() {
  CHANGED=0
  if ! dpkg -s raspotify >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 10 --max-time 60 \
      https://dtcooper.github.io/raspotify/key.asc -o "$RASPOTIFY_KEY.tmp" || return 1
    install -m 0644 "$RASPOTIFY_KEY.tmp" "$RASPOTIFY_KEY" || return 1
    rm -f "$RASPOTIFY_KEY.tmp"
    echo "deb [signed-by=$RASPOTIFY_KEY] https://dtcooper.github.io/raspotify raspotify main" \
      >/etc/apt/sources.list.d/raspotify.list || return 1
    timeout 300 apt-get update -qq || return 1
    timeout 300 apt-get install -y -qq raspotify >/dev/null || return 1
    CHANGED=1
  fi
  # Same rule as asound.conf: a file with the marker is ours to rewrite, any other file is kept.
  # No LIBRESPOT_DISABLE_CREDENTIAL_CACHE: the speaker only shows up in the Web API device list
  # after it was picked once in a Spotify app, and that login must survive restarts.
  if [[ ! -e /etc/raspotify/conf ]] || grep -q '^# spookhub-managed' /etc/raspotify/conf; then
    install -d /etc/raspotify || return 1
    put_file /etc/raspotify/conf <<CONF || return 1
# spookhub-managed
# Written by Spookhub install.sh. Remove the marker line above to keep your own settings.
LIBRESPOT_NAME="${JUKEBOX_NAME}"
# The sink plays on Spookhub's Setup → Audio output (scripts/music-sink.sh, aplay on the dmix).
LIBRESPOT_BACKEND=subprocess
LIBRESPOT_DEVICE="$APP/scripts/music-sink.sh"
LIBRESPOT_BITRATE=320
LIBRESPOT_INITIAL_VOLUME=70
LIBRESPOT_SYSTEM_CACHE=/var/cache/raspotify
LIBRESPOT_ENABLE_VOLUME_NORMALISATION=
LIBRESPOT_DISABLE_AUDIO_CACHE=
LIBRESPOT_QUIET=
CONF
    echo "/etc/raspotify/conf: speaker \"${JUKEBOX_NAME}\", sink follows Spookhub audio output"
  else
    echo "keeping existing /etc/raspotify/conf (not spookhub-managed)"
  fi
  # The packaged unit uses DynamicUser. dmix needs group audio (/dev/snd, ipc_gid audio) and the
  # credential cache needs a persistent writable dir. Restated so an upstream unit change can't
  # silently break either.
  install -d /etc/systemd/system/raspotify.service.d || return 1
  put_file /etc/systemd/system/raspotify.service.d/spookhub.conf <<'CONF' || return 1
[Service]
SupplementaryGroups=audio
CacheDirectory=raspotify
CONF
  systemctl daemon-reload || return 1
  systemctl enable raspotify.service >/dev/null 2>&1 || return 1
  if [[ $CHANGED -eq 1 ]] || ! systemctl is-active --quiet raspotify.service; then
    systemctl restart raspotify.service || return 1
  else
    echo "raspotify config unchanged; not restarting (music keeps playing)"
  fi
}
# Speaker name follows [jukebox] device_name so the backend finds the device by name.
JUKEBOX_NAME="$(config_value jukebox device_name)"
JUKEBOX_NAME="${JUKEBOX_NAME//[\"\\\$\`]/}"
JUKEBOX_NAME="${JUKEBOX_NAME:-Spookhub}"
if [[ "${SPOOKHUB_SPOTIFY:-1}" == 0 ]]; then
  echo "skipped (SPOOKHUB_SPOTIFY=0)"
elif install_raspotify; then
  echo "raspotify $(systemctl is-active raspotify 2>/dev/null || true)"
else
  rm -f "$RASPOTIFY_KEY.tmp"
  warn "raspotify setup failed: Spookhub works, the jukebox has no speaker. Re-run install.sh later."
fi

log "Jukebox: port 80 forward"
# Guests type http://<hub-ip>/music without a port. systemd owns :80 and hands connections to
# systemd-socket-proxyd, which forwards to the app; the app itself stays unprivileged.
PROXYD=/usr/lib/systemd/systemd-socket-proxyd
[[ -x "$PROXYD" ]] || PROXYD=/lib/systemd/systemd-socket-proxyd
PORT80=0
install_port80() {
  [[ -x "$PROXYD" ]] || { warn "systemd-socket-proxyd not found"; return 1; }
  # Our own socket on :80 is fine. Any other listener (nginx, apache, ...) keeps the port.
  if ! systemctl is-active --quiet spookhub-http.socket \
    && [[ -n "$(ss -ltnH 'sport = :80' 2>/dev/null || true)" ]]; then
    warn "something else listens on port 80; skipping the forward:"
    ss -ltnpH 'sport = :80' >&2 2>/dev/null || true
    return 2
  fi
  CHANGED=0
  put_file /etc/systemd/system/spookhub-http.socket <<'UNIT' || return 1
# spookhub-managed (scripts/install.sh)
[Unit]
Description=Spookhub port 80 forward (guest URL http://<hub>/music)

[Socket]
ListenStream=80

[Install]
WantedBy=sockets.target
UNIT
  put_file /etc/systemd/system/spookhub-http.service <<UNIT || return 1
# spookhub-managed (scripts/install.sh)
[Unit]
Description=Spookhub port 80 forward to 127.0.0.1:${PORT}
Requires=spookhub-http.socket
After=spookhub-http.socket

[Service]
ExecStart=${PROXYD} 127.0.0.1:${PORT}
PrivateNetwork=no
DynamicUser=yes
UNIT
  systemctl daemon-reload || return 1
  systemctl enable --now spookhub-http.socket >/dev/null 2>&1 || return 1
  # A changed app port reaches an already running proxy only through a restart; an unchanged one
  # keeps open guest connections alive.
  if [[ $CHANGED -eq 1 ]]; then
    systemctl try-restart spookhub-http.service || return 1
  fi
}
if [[ "${SPOOKHUB_PORT80:-1}" == 0 ]]; then
  echo "skipped (SPOOKHUB_PORT80=0)"
  if systemctl is-enabled --quiet spookhub-http.socket 2>/dev/null; then
    systemctl disable --now spookhub-http.socket spookhub-http.service >/dev/null 2>&1 || true
    echo "disabled the existing spookhub-http.socket"
  fi
else
  rc=0
  install_port80 || rc=$?
  if [[ $rc -eq 0 ]]; then
    PORT80=1
    echo "port 80 -> 127.0.0.1:${PORT}"
  elif [[ $rc -eq 1 ]]; then
    warn "port 80 forward failed; guests use the URL with :${PORT}"
  fi
fi

log "Keeping the hub awake"
# Console blanking would turn the projector picture off when mpv is idle.
if [[ -f /etc/default/grub ]] && ! grep -q 'consoleblank=0' /etc/default/grub; then
  sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"/GRUB_CMDLINE_LINUX_DEFAULT="\1 consoleblank=0 vt.global_cursor_default=0"/' /etc/default/grub
  command -v update-grub >/dev/null && update-grub >/dev/null 2>&1 || true
fi
setterm --blank 0 --powerdown 0 --cursor off </dev/tty1 >/dev/tty1 2>/dev/null || true
systemctl mask --now sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null
install -d /etc/systemd/logind.conf.d
cat >/etc/systemd/logind.conf.d/spookhub.conf <<'CONF'
[Login]
HandleSuspendKey=ignore
HandleHibernateKey=ignore
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
IdleAction=ignore
CONF

log "systemd unit"
install -m 0644 "$APP/deploy/spookhub.service" /etc/systemd/system/spookhub.service
systemctl enable systemd-networkd-wait-online.service >/dev/null 2>&1 || true
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl enable spookhub.service >/dev/null
systemctl restart spookhub.service

sleep 2
systemctl is-active --quiet spookhub && STATUS="running" || STATUS="NOT running (journalctl -u spookhub -e)"
IP="$(hostname -I | awk '{print $1}')"
log "Done. Spookhub $COMMIT is $STATUS"
echo "  Open:  http://${IP}:${PORT}   or   http://$(hostname).local:${PORT}"
if [[ $PORT80 -eq 1 ]]; then
  echo "  Guests: http://${IP}/music"
else
  echo "  Guests: http://${IP}:${PORT}/music"
fi
echo "  Logs:  journalctl -u spookhub -f"
echo "  A reboot applies the console-blanking kernel option."
