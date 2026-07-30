#!/usr/bin/env bash
#
# PrntBtlr self-updater — download a release tarball from GitHub and redeploy.
#
# Called by the control panel (System → Updates) inside a transient systemd
# unit, so the service restart at the end can't kill the update mid-way. It can
# also be run by hand:
#
#   sudo /opt/prntbtlr/update.sh v0.2.0            # stable release
#   sudo /opt/prntbtlr/update.sh v0.2.0-beta.3     # beta release
#
# It fetches the tag's tarball, stamps the release version into the app, and
# re-runs the bundled installer with SKIP_APT=1 (pass SKIP_APT=0 to also
# refresh system packages). Everything is logged to /var/log/prntbtlr-update.log.
set -euo pipefail

TAG="${1:?usage: update.sh vX.Y.Z[-beta.N]}"
REPO="${PRNTBTLR_UPDATE_REPO:-w0rkingchr1s/prntbtlr}"
LOG_FILE=/var/log/prntbtlr-update.log

[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-beta\.[0-9]+)?$ ]] || {
  echo "Invalid release tag: $TAG (expected vX.Y.Z or vX.Y.Z-beta.N)" >&2
  exit 2
}
[ "$(id -u)" -eq 0 ] || { echo "Please run as root (sudo update.sh $TAG)." >&2; exit 1; }

exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== PrntBtlr update to $TAG @ $(date '+%Y-%m-%d %H:%M:%S') ==="

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading $REPO @ $TAG…"
curl -fsSL --retry 3 "https://codeload.github.com/$REPO/tar.gz/refs/tags/$TAG" \
  -o "$TMP/src.tar.gz"
mkdir "$TMP/src"
tar -xzf "$TMP/src.tar.gz" -C "$TMP/src" --strip-components=1

# Stamp the release version so the panel knows exactly what is installed
# (the updater compares this against GitHub release tags). Releases from before
# the stamper existed don't ship it — fall back to writing __init__.py directly,
# so installing (or rolling back to) an older tag still works.
VERSION="${TAG#v}"
if [ -f "$TMP/src/scripts/stamp-version.sh" ]; then
  bash "$TMP/src/scripts/stamp-version.sh" "$VERSION" "$TMP/src"
else
  sed -i "s/^__version__ = .*/__version__ = \"$VERSION\"/" "$TMP/src/app/__init__.py"
  echo "Stamped version $VERSION (legacy tarball, no stamp-version.sh)"
fi

# The installer is idempotent and doubles as the upgrade path; it redeploys
# /opt/prntbtlr, restarts the service and health-checks the panel. It derives the
# version stamp from git when it finds a checkout, so pass the release version
# explicitly — the tarball is authoritative here, whatever it was unpacked into.
SKIP_APT="${SKIP_APT:-1}" NO_FIREWALL="${NO_FIREWALL:-1}" PRNTBTLR_VERSION="$VERSION" \
  bash "$TMP/src/scripts/install.sh"

echo "=== PrntBtlr update to $TAG finished ==="
