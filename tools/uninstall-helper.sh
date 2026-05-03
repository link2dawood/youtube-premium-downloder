#!/bin/bash
#
# Uninstall the PixelCatch Helper.
#
# Removes the helper files installed by PixelCatch-Helper-*.pkg, deletes the
# Chrome native messaging registration, and (optionally) wipes log files.
#
# Usage:
#   sudo ./tools/uninstall-helper.sh           # remove helper + NM manifest
#   sudo ./tools/uninstall-helper.sh --logs    # also delete the log files
#
# This script is for the *helper* only — uninstalling the Chrome extension
# itself is a one-click action in chrome://extensions.
#
set -euo pipefail

REMOVE_LOGS=0
[ "${1:-}" = "--logs" ] && REMOVE_LOGS=1

if [ "$EUID" -ne 0 ]; then
    echo "This script must be run with sudo (it needs to remove files in /Library)." >&2
    exit 1
fi

INSTALL_DIR="/Library/Application Support/PixelCatch"
NMH_SYSTEM_DIR="/Library/Google/Chrome/NativeMessagingHosts"
HOST_NAME="com.pixelcatch.downloader"

removed=0

if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "Removed $INSTALL_DIR"
    removed=$((removed + 1))
fi

if [ -f "$NMH_SYSTEM_DIR/$HOST_NAME.json" ]; then
    rm -f "$NMH_SYSTEM_DIR/$HOST_NAME.json"
    echo "Removed $NMH_SYSTEM_DIR/$HOST_NAME.json"
    removed=$((removed + 1))
fi

# Per-user manifests written by older versions of native/install.sh.
for home in /Users/*; do
    [ -d "$home" ] || continue
    user_nmh="$home/Library/Application Support/Google/Chrome/NativeMessagingHosts"
    if [ -f "$user_nmh/$HOST_NAME.json" ]; then
        rm -f "$user_nmh/$HOST_NAME.json"
        echo "Removed $user_nmh/$HOST_NAME.json"
        removed=$((removed + 1))
    fi
    legacy="$user_nmh/com.extension.ytdownloader.json"
    if [ -f "$legacy" ]; then
        rm -f "$legacy"
        echo "Removed legacy: $legacy"
    fi
done

if [ "$REMOVE_LOGS" -eq 1 ]; then
    for home in /Users/*; do
        log="$home/Library/Logs/com.pixelcatch.downloader.log"
        if [ -f "$log" ]; then
            rm -f "$log"
            echo "Removed log: $log"
        fi
    done
fi

if [ "$removed" -eq 0 ]; then
    echo "PixelCatch Helper does not appear to be installed."
else
    echo
    echo "PixelCatch Helper uninstalled."
fi
