#!/usr/bin/env bash
#
# Uninstall the PixelCatch Helper on Linux.
#
# Usage:
#   ./tools/uninstall-linux.sh           # remove helper + NM manifests
#   ./tools/uninstall-linux.sh --logs    # also delete the log file
#
set -euo pipefail

REMOVE_LOGS=0
[ "${1:-}" = "--logs" ] && REMOVE_LOGS=1

INSTALL_DIR="$HOME/.local/share/PixelCatch"
HOST_NAME="com.pixelcatch.downloader"

NMH_DIRS=(
    "$HOME/.config/google-chrome/NativeMessagingHosts"
    "$HOME/.config/chromium/NativeMessagingHosts"
    "$HOME/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts"
    "$HOME/.config/microsoft-edge/NativeMessagingHosts"
    "$HOME/.config/vivaldi/NativeMessagingHosts"
)

removed=0

if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "Removed $INSTALL_DIR"
    removed=$((removed + 1))
fi

for dir in "${NMH_DIRS[@]}"; do
    if [ -f "$dir/$HOST_NAME.json" ]; then
        rm -f "$dir/$HOST_NAME.json"
        echo "Unregistered: $dir/$HOST_NAME.json"
        removed=$((removed + 1))
    fi
    # Sweep the legacy name too.
    [ -f "$dir/com.extension.ytdownloader.json" ] && rm -f "$dir/com.extension.ytdownloader.json"
done

if [ "$REMOVE_LOGS" -eq 1 ]; then
    log="${XDG_STATE_HOME:-$HOME/.local/state}/pixelcatch/downloader.log"
    if [ -f "$log" ]; then
        rm -f "$log"
        echo "Removed log: $log"
    fi
fi

if [ "$removed" -eq 0 ]; then
    echo "PixelCatch Helper does not appear to be installed."
else
    echo
    echo "PixelCatch Helper uninstalled."
fi
