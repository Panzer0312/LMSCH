#!/usr/bin/env bash
# Full system on a desktop: 3 simulated WLED boards, backend with windowed mpv, Vite dev server.
# Usage: scripts/dev.sh [--no-mpv]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${SPOOKHUB_DEV_DATA:-$ROOT/.devdata}"
mkdir -p "$DATA"

export SPOOKHUB_CONFIG=/nonexistent
export SPOOKHUB_PATHS__DATA_DIR="$DATA"
export SPOOKHUB_MPV__SOCKET_PATH="$DATA/mpv.sock"
export SPOOKHUB_MPV__VO="${SPOOKHUB_MPV__VO:-gpu}"
export SPOOKHUB_MPV__GPU_CONTEXT="${SPOOKHUB_MPV__GPU_CONTEXT:-auto}"
export SPOOKHUB_MPV__AO="${SPOOKHUB_MPV__AO:-pipewire}"
export SPOOKHUB_MPV__EXTRA_ARGS='["--no-fullscreen","--geometry=640x360"]'
[[ "${1:-}" == "--no-mpv" ]] && export SPOOKHUB_MPV__ENABLED=false

trap 'kill 0' EXIT
(cd "$ROOT/backend" && uv run python ../simulator/wled_sim.py --boards 3 --base-port 9001) &
(cd "$ROOT/backend" && uv run spookhub) &
(cd "$ROOT/frontend" && npm run dev) &
echo "UI: http://localhost:5173  (add boards by IP: 127.0.0.1:9001 / 9002 / 9003)"
wait
