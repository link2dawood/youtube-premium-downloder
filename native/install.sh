#!/bin/bash
# Registers the yt-dlp native messaging host with Chrome.
# Usage: ./install.sh <extension-id>
#
# Find your extension ID at chrome://extensions (enable Developer mode).

set -e

if [ -z "$1" ]; then
  echo "Usage: ./install.sh <extension-id>"
  echo ""
  echo "  1. Open Chrome and go to chrome://extensions"
  echo "  2. Enable Developer mode (top-right toggle)"
  echo "  3. Find your extension and copy its ID"
  echo "  4. Run: ./install.sh <that-id>"
  exit 1
fi

EXTENSION_ID="$1"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST_NAME="com.extension.ytdownloader.json"
NMH_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
RUNNER="$DIR/runner.sh"

# Locate python3 with absolute path (Chrome runs with a restricted PATH)
PYTHON3="$(which python3 2>/dev/null || true)"
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$candidate" ]; then
    PYTHON3="$candidate"
    break
  fi
done

if [ -z "$PYTHON3" ]; then
  echo "Error: python3 not found. Install it with: brew install python"
  exit 1
fi

echo "Using Python: $PYTHON3"

# Generate a wrapper shell script so Chrome doesn't need python3 in its PATH
cat > "$RUNNER" <<WRAPPER
#!/bin/bash
exec "$PYTHON3" "$DIR/downloader.py"
WRAPPER
chmod +x "$RUNNER"
chmod +x "$DIR/downloader.py"
mkdir -p "$NMH_DIR"

# Write the native messaging host manifest pointing at the wrapper
cat > "$NMH_DIR/$MANIFEST_NAME" <<JSON
{
  "name": "com.extension.ytdownloader",
  "description": "Downloads YouTube member-only videos via yt-dlp using Chrome cookies",
  "path": "$RUNNER",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://$EXTENSION_ID/"
  ]
}
JSON

echo "Native messaging host installed."
echo "  Manifest: $NMH_DIR/$MANIFEST_NAME"
echo "  Runner:   $RUNNER"
echo "  Python:   $PYTHON3"
echo ""
echo "Make sure yt-dlp is installed: brew install yt-dlp"
echo "Reload the extension in Chrome (chrome://extensions -> refresh icon)."
