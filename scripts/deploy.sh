#!/usr/bin/env bash
# Build the frontend locally, push the project to the hub over SSH and restart the service.
# Usage: scripts/deploy.sh user@minipc [--install]
#   --install  run scripts/install.sh on the hub (first deploy, or after dependency changes)
set -euo pipefail

TARGET="${1:?usage: $0 user@host [--install]}"
MODE="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_SRC="~/spookhub-src"

echo "==> building frontend"
(cd "$ROOT/frontend" && npm ci --silent && npm run build --silent)
"$ROOT/scripts/frontend-hash.sh" > "$ROOT/frontend/dist/.build-hash"

echo "==> syncing to $TARGET:$REMOTE_SRC"
rsync -az --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude '*.db' \
  "$ROOT/" "$TARGET:$REMOTE_SRC/"

if [[ "$MODE" == "--install" ]]; then
  ssh -t "$TARGET" "sudo bash $REMOTE_SRC/scripts/install.sh"
else
  # Fast path: copy code, reinstall the package (no new apt packages), restart.
  ssh -t "$TARGET" "sudo rsync -a --delete --exclude venv --exclude 'frontend/src' $REMOTE_SRC/ /opt/spookhub/ \
    && sudo /opt/spookhub/venv/bin/pip install -q /opt/spookhub/backend \
    && sudo systemctl restart spookhub && systemctl is-active spookhub"
fi
echo "==> deployed"
