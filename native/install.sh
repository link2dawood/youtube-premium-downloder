#!/bin/bash
# Registers the PixelCatch native messaging host with Chrome.
# Usage: ./install.sh <extension-id>
#
# Find your extension ID at chrome://extensions (enable Developer mode).
# Without a manifest "key" the unpacked ID is per-machine, so re-run this
# script if you ever delete and re-add the extension.

set -euo pipefail

if [ $# -lt 1 ] || [ -z "${1:-}" ]; then
  echo "Usage: ./install.sh <extension-id>"
  echo
  echo "  1. Open Chrome and visit chrome://extensions"
  echo "  2. Enable Developer mode (top-right toggle)"
  echo "  3. Find PixelCatch and copy its ID"
  echo "  4. Run: ./install.sh <that-id>"
  exit 1
fi

EXTENSION_ID="$1"
HOST_NAME="com.pixelcatch.downloader"
MANIFEST_NAME="$HOST_NAME.json"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NMH_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
LOG_DIR="$HOME/Library/Logs"
RUNNER="$DIR/runner.sh"

# Locate python3 (Chrome runs the host with a restricted PATH, so resolve it
# to an absolute path now and bake it into the runner).
PYTHON3="$(command -v python3 2>/dev/null || true)"
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$candidate" ]; then
    PYTHON3="$candidate"
    break
  fi
done

if [ -z "$PYTHON3" ]; then
  echo "Error: python3 not found. Install with: brew install python"
  exit 1
fi

echo "Using Python: $PYTHON3"

# Generate a wrapper shell script so Chrome doesn't need python3 in its PATH.
cat > "$RUNNER" <<WRAPPER
#!/bin/bash
set -e
exec "$PYTHON3" "$DIR/downloader.py"
WRAPPER
chmod +x "$RUNNER"
chmod +x "$DIR/downloader.py"

mkdir -p "$NMH_DIR"
mkdir -p "$LOG_DIR"

# Clean up the legacy host name from prior versions, if present.
LEGACY_MANIFEST="$NMH_DIR/com.extension.ytdownloader.json"
if [ -f "$LEGACY_MANIFEST" ]; then
  rm -f "$LEGACY_MANIFEST"
  echo "Removed legacy native host manifest: $LEGACY_MANIFEST"
fi

# Native messaging manifest pointing Chrome at the wrapper.
cat > "$NMH_DIR/$MANIFEST_NAME" <<JSON
{
  "name": "$HOST_NAME",
  "description": "PixelCatch YouTube downloader native host",
  "path": "$RUNNER",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://$EXTENSION_ID/"
  ]
}
JSON

echo
echo "PixelCatch native host installed."
echo "  Manifest: $NMH_DIR/$MANIFEST_NAME"
echo "  Runner:   $RUNNER"
echo "  Python:   $PYTHON3"
echo "  Logs:     $LOG_DIR/com.pixelcatch.downloader.log"
echo
echo "Next steps:"
echo "  1. Make sure yt-dlp is installed: brew install yt-dlp"
echo "  2. Reload PixelCatch in chrome://extensions (refresh icon)"
