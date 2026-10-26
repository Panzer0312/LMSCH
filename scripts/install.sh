#!/usr/bin/env bash
# Spookhub installer for a fresh Debian 13 (trixie) minimal system. Idempotent: safe to re-run.
# Usage (as root, from the project directory): sudo ./scripts/install.sh
set -euo pipefail

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
  ca-certificates rsync curl >/dev/null
# Intel VA-API: iHD for Broadwell+ (non-free), i965 for older chips. Install whatever exists.
if grep -qi intel /proc/cpuinfo; then
  apt-get install -y -qq intel-media-va-driver >/dev/null 2>&1 \
    || apt-get install -y -qq intel-media-va-driver-non-free >/dev/null 2>&1 || true
  apt-get install -y -qq i965-va-driver >/dev/null 2>&1 || true
fi
apt-get install -y -qq vainfo >/dev/null 2>&1 || true

log "Creating user and directories"
id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$USER_NAME"
for g in video render audio; do getent group "$g" >/dev/null && usermod -aG "$g" "$USER_NAME"; done
install -d -m 0755 "$APP" "$ETC"
install -d -m 0750 -o "$USER_NAME" -g "$USER_NAME" "$DATA" "$DATA/media"

log "Copying application to $APP"
rsync -a --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude 'frontend/src' --exclude '/venv' \
  "$SRC/" "$APP/"

if [[ ! -f "$APP/frontend/dist/index.html" ]]; then
  if command -v npm >/dev/null; then
    log "Building frontend"
    (cd "$SRC/frontend" && npm ci && npm run build) && rsync -a "$SRC/frontend/dist/" "$APP/frontend/dist/"
  else
    echo "WARNING: frontend/dist missing and npm not installed. Build it on the dev machine" \
         "(scripts/deploy.sh does this) — the API still works without it."
  fi
fi

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
log "Done. Spookhub is $STATUS"
echo "  Open:  http://${IP}:${PORT}   or   http://$(hostname).local:${PORT}"
echo "  Logs:  journalctl -u spookhub -f"
echo "  A reboot applies the console-blanking kernel option."
