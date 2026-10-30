#!/usr/bin/env bash
# Read-only hub diagnostics. Needs no root and never exits non-zero because a check failed.
set -u

section() { printf '\n== %s ==\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

section "mpv"
if have mpv; then mpv --version 2>&1 | head -n 1; else echo "missing"; fi

section "vainfo"
if have vainfo; then
  vainfo 2>&1 | grep -Ei 'VA-API version|Driver version|VAProfile|error|failed' | head -n 12 || true
else
  echo "missing"
fi

section "DRM connectors"
found=0
for d in /sys/class/drm/card*-*; do
  [ -e "$d/status" ] || continue
  found=1
  printf '%s: %s\n' "${d##*/}" "$(cat "$d/status" 2>/dev/null || echo unknown)"
done
[ "$found" = 1 ] || echo "none found"

section "audio (aplay -l)"
if have aplay; then aplay -l 2>&1 | head -n 20; else echo "missing"; fi

section "disk"
dir=/var/lib/spookhub
[ -d "$dir" ] || dir=/
df -h "$dir" 2>&1 | tail -n 1 | awk -v d="$dir" '{print d ": " $4 " free of " $2 " (" $5 " used)"}'

section "time"
if have timedatectl; then
  timedatectl show -p NTPSynchronized 2>/dev/null || timedatectl 2>&1 | grep -i synchronized || echo "unknown"
else
  echo "timedatectl missing"
fi

section "service"
if have systemctl; then
  systemctl is-active spookhub 2>&1 || true
else
  echo "systemctl missing"
fi

section "jukebox"
if have systemctl; then
  printf 'raspotify: %s\n' "$(systemctl is-active raspotify 2>/dev/null || true)"
  printf 'spookhub-http.socket: %s\n' "$(systemctl is-active spookhub-http.socket 2>/dev/null || true)"
fi
if have ss; then
  l80="$(ss -ltnH 'sport = :80' 2>/dev/null | awk '{print $4}' | paste -sd ' ' -)"
  printf ':80 listener: %s\n' "${l80:-none}"
else
  echo "ss missing"
fi

section "version"
if [ -r /opt/spookhub/version.json ]; then cat /opt/spookhub/version.json; echo; else echo "/opt/spookhub/version.json not found"; fi

exit 0
