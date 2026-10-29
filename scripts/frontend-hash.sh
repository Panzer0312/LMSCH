#!/usr/bin/env bash
# Prints a hash of every input that affects the frontend build. install.sh compares it with the
# stamp stored in frontend/dist/.build-hash to decide whether a rebuild is needed.
set -euo pipefail
FE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../frontend" && pwd)"
cd "$FE"
# Only existing inputs: find exits non-zero on a missing path (e.g. no public/), and under
# pipefail that aborted install.sh without a message.
inputs=()
for p in src public index.html package.json package-lock.json vite.config.ts tsconfig*.json .oxlintrc.json; do
  [[ -e "$p" ]] && inputs+=("$p")
done
find "${inputs[@]}" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16
