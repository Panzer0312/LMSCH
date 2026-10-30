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
PORT=8080

[[ $EUID -eq 0 ]] || { echo "run as root (sudo $0)"; exit 1; }
log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

log "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
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
  cat >/etc/asound.conf <<CONF
# spookhub-managed
# Written by Spookhub install.sh (dmix default so several players share the card).
# Remove the marker line above to keep install.sh from rewriting this file.
pcm.!default {
  type plug
  slave.pcm "spookmix"
}
pcm.spookmix {
  type dmix
  ipc_key 5150
  ipc_gid audio
  ipc_perm 0660
  slave {
    pcm "hw:${ALSA_CARD},${ALSA_DEVICE}"
    rate 48000
  }
}
ctl.!default {
  type hw
  card ${ALSA_CARD}
}
CONF
  echo "wrote /etc/asound.conf (dmix on hw:${ALSA_CARD},${ALSA_DEVICE})"
else
  echo "keeping existing /etc/asound.conf (not spookhub-managed): make sure it defines a dmix default"
fi
echo "note: mpv audio device must be 'auto' or 'alsa/default' so all players share dmix;"
echo "      raw 'alsa/hdmi:...' or 'alsa/hw:...' devices grab the card and block the effects players."

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
echo "  Logs:  journalctl -u spookhub -f"
echo "  A reboot applies the console-blanking kernel option."
