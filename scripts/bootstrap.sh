#!/usr/bin/env bash
#
# PrntBtlr bootstrap — one command for a truly blank system.
#
# Installs git if needed, clones (or updates) the repo, then runs the installer.
# Run it straight from the internet:
#
#   curl -fsSL https://raw.githubusercontent.com/w0rkingchr1s/prntbtlr/main/scripts/bootstrap.sh | sudo bash
#
# Override the clone target or branch:
#   sudo PRNTBTLR_DIR=/opt/src/prntbtlr PRNTBTLR_REF=main bash bootstrap.sh
#
set -euo pipefail

REPO_URL=${PRNTBTLR_REPO:-https://github.com/w0rkingchr1s/prntbtlr.git}
TARGET=${PRNTBTLR_DIR:-/opt/prntbtlr-src}
REF=${PRNTBTLR_REF:-main}

red=$'\033[1;31m'; grn=$'\033[1;32m'; off=$'\033[0m'
[ "$(id -u)" -eq 0 ] || { echo "${red}Run as root (sudo).${off}" >&2; exit 1; }

if ! command -v git >/dev/null 2>&1; then
  echo "==> Installing git…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y && apt-get install -y --no-install-recommends git ca-certificates
fi

if [ -d "$TARGET/.git" ]; then
  echo "==> Updating existing checkout in $TARGET…"
  git -C "$TARGET" fetch --depth 1 origin "$REF"
  git -C "$TARGET" checkout -B "$REF" "origin/$REF"
else
  echo "==> Cloning $REPO_URL into $TARGET…"
  git clone --depth 1 --branch "$REF" "$REPO_URL" "$TARGET"
fi

echo "==> Running installer…"
chmod +x "$TARGET/scripts/install.sh"
"$TARGET/scripts/install.sh"

echo "${grn}Bootstrap complete.${off}"
