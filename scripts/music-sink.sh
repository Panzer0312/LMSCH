#!/bin/sh
# raspotify (librespot) subprocess sink: raw S16LE, 44.1 kHz, stereo on stdin.
# Plays on the output picked in Spookhub Setup → Audio output, written by the hub to the file
# below. librespot starts this per play (and after a pause), so a change applies on the next play.
dev="$(head -n 1 /var/lib/spookhub-audio/device 2>/dev/null)"
case "$dev" in
  alsa/*) pcm="${dev#alsa/}" ;;
  *) pcm=default ;;
esac
# aplay opens the device before reading stdin: a busy or vanished device falls back to the shared
# default instead of killing librespot (which then skips through the queue). stderr lands in the
# raspotify journal (scripts/hubcheck.sh shows it).
aplay -q -t raw -f S16_LE -r 44100 -c 2 -D "$pcm" \
  || exec aplay -q -t raw -f S16_LE -r 44100 -c 2 -D default
