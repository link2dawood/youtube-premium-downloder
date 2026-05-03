#!/usr/bin/env bash
#
# Updates the on-disk macOS PixelCatch helper from the current repo
# without rebuilding the whole .pkg. Useful during development when
# you've changed downloader.py and want to test it immediately.
#
# Usage:
#   sudo bash tools/update-mac-helper.sh
#
# Safe to re-run. Strips quarantine after install so Gatekeeper doesn't
# kill yt-dlp on the next launch.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Run with sudo: sudo bash tools/update-mac-helper.sh" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/native/downloader.py"
DEST_DIR="/Library/Application Support/PixelCatch"
DEST="$DEST_DIR/downloader.py"

if [ ! -f "$SRC" ]; then
    echo "Source not found: $SRC" >&2
    echo "Run this script from inside the repo." >&2
    exit 1
fi

if [ ! -d "$DEST_DIR" ]; then
    echo "PixelCatch helper isn't installed at $DEST_DIR" >&2
    echo "Install the .pkg first, then re-run this script." >&2
    exit 1
fi

install -m 755 "$SRC" "$DEST"
echo "Updated: $DEST"

# Strip quarantine on the install dir so freshly-copied files don't
# trip Gatekeeper at runtime.
xattr -dr com.apple.quarantine "$DEST_DIR" 2>/dev/null || true

# Sanity-check the new code is actually in place.
if grep -q "player_client=tv" "$DEST"; then
    echo "Verified: new code is live (player_client=tv present)."
else
    echo "Warning: 'player_client=tv' not found in installed downloader." >&2
    echo "Maybe the repo's downloader.py is also stale — check git status." >&2
    exit 1
fi
