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

# Full history and tags, deliberately not a shallow clone: the installer derives
# the version it stamps into the panel from the release tags, and `git describe`
# can't place HEAD when the tagged commits are cut out of the history. The repo
# is a couple of megabytes either way, so there is nothing to save here.
if [ -d "$TARGET/.git" ]; then
  echo "==> Updating existing checkout in $TARGET…"
  if [ -e "$TARGET/.git/shallow" ]; then
    # Left over from an older bootstrap run — give it its history back.
    git -C "$TARGET" fetch --unshallow --tags origin || git -C "$TARGET" fetch --tags origin
  else
    git -C "$TARGET" fetch --tags origin "$REF"
  fi
  git -C "$TARGET" checkout -B "$REF" "origin/$REF"
else
  echo "==> Cloning $REPO_URL into $TARGET…"
  git clone --branch "$REF" "$REPO_URL" "$TARGET"
fi

echo "==> Running installer…"
chmod +x "$TARGET/scripts/install.sh"
"$TARGET/scripts/install.sh"

echo "${grn}Bootstrap complete.${off}"
