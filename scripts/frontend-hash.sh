#!/usr/bin/env bash
# Prints a hash of every input that affects the frontend build. install.sh compares it with the
# stamp stored in frontend/dist/.build-hash to decide whether a rebuild is needed.
set -euo pipefail
FE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../frontend" && pwd)"
cd "$FE"
find src public index.html package.json package-lock.json vite.config.ts tsconfig*.json .oxlintrc.json \
  -type f -print0 2>/dev/null | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16
