#!/bin/sh
port="${1:-8080}"
data=/var/lib/spookhub
keyfile="$data/player.key"
while [ ! -s "$keyfile" ]; do sleep 2; done
key="$(head -n 1 "$keyfile")"
url="http://127.0.0.1:${port}/api/jukebox/player"
until curl -fsS -o /dev/null --max-time 5 "$url"; do sleep 2; done
dev="$(head -n 1 /var/lib/spookhub-audio/device 2>/dev/null)"
case "$dev" in
  alsa/*) pcm="${dev#alsa/}" ;;
  *) pcm=default ;;
esac
exec xvfb-run -a -s "-screen 0 800x600x24 -nolisten tcp" google-chrome-stable \
  --user-data-dir="$data/player-profile" \
  --no-first-run --no-default-browser-check --password-store=basic \
  --disable-gpu --disable-dev-shm-usage --disable-features=Translate,MediaRouter \
  --autoplay-policy=no-user-gesture-required \
  --alsa-output-device="$pcm" \
  --enable-logging=stderr --v=0 \
  "${url}?key=${key}"
