#!/usr/bin/env bash
#
# PrntBtlr version stamper — write a version into a source tree.
#
# The panel displays `app.__version__` and the self-updater compares it against
# the GitHub release tags, so an install that doesn't carry its real version
# both lies in the UI and confuses the updater (an install stuck at 0.1.0 is
# offered every release forever, including the one it is already running).
#
# This is the single place that writes the version. Used by:
#   - .github/workflows/cut-release.yml  — stamps + commits before tagging
#   - .github/workflows/promote.yml      — stamps the stable version on promote
#   - .github/workflows/release.yml      — stamps the image build tree
#   - scripts/update.sh                  — release tarballs
#   - scripts/install.sh                 — git checkouts (derived from tags)
#
# Usage:
#   scripts/stamp-version.sh 0.4.0                    # stamp its own tree
#   scripts/stamp-version.sh 0.4.0-beta.2 /tmp/src    # stamp another tree
#   scripts/stamp-version.sh 0.4.0+dev.3.gabc1234     # unreleased build
#
set -euo pipefail

VERSION="${1:?usage: stamp-version.sh X.Y.Z[-beta.N][+local] [tree-root]}"
ROOT="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Same shape the updater accepts, plus an optional local part for builds that
# are not a release (see install.sh). Ordering ignores the local part.
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-beta\.[0-9]+)?(\+[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$ ]]; then
  echo "Invalid version: '$VERSION' (expected X.Y.Z, X.Y.Z-beta.N, optionally +local)" >&2
  exit 2
fi

INIT="$ROOT/app/__init__.py"
PYPROJECT="$ROOT/pyproject.toml"

[ -f "$INIT" ] || { echo "Not a PrntBtlr tree: $INIT does not exist" >&2; exit 1; }

# `__version__` carries the tag spelling (0.4.0-beta.2) because the updater
# compares it against release tag names. pyproject needs PEP 440 (0.4.0b2) —
# the same version, spelled the way Python packaging normalizes it.
PEP440="${VERSION/-beta./b}"

sed -i "s/^__version__ = .*/__version__ = \"$VERSION\"/" "$INIT"
grep -qxF "__version__ = \"$VERSION\"" "$INIT" \
  || { echo "Failed to stamp $INIT — no __version__ assignment found?" >&2; exit 1; }

if [ -f "$PYPROJECT" ]; then
  # First `version = ` line only: that is [project].version. Other keys in the
  # file (requires-python, target-version) don't match the anchored pattern.
  sed -i "0,/^version = /s/^version = .*/version = \"$PEP440\"/" "$PYPROJECT"
  grep -qxF "version = \"$PEP440\"" "$PYPROJECT" \
    || { echo "Failed to stamp $PYPROJECT — no [project] version found?" >&2; exit 1; }
fi

echo "Stamped version $VERSION (pyproject: $PEP440) into $ROOT"
