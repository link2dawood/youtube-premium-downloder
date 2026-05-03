#!/usr/bin/env bash
#
# PixelCatch Helper installer for Linux.
#
# Works on any distro with Python 3 — Debian, Ubuntu, Fedora, Arch, openSUSE,
# you name it. Detects which Chromium-family browser(s) you have installed
# (Chrome, Chromium, Brave, Microsoft Edge) and registers the helper with
# each one.
#
# Usage:
#   ./tools/install-linux.sh --extension-id <id>[,<id2>,...]
#
# Find your extension ID at chrome://extensions (Developer mode).
#
# Per-user install (no sudo). Files go in:
#   ~/.local/share/PixelCatch/                       — helper + bundled yt-dlp
#   ~/.config/google-chrome/NativeMessagingHosts/    — NM manifest (Chrome)
#   ~/.config/chromium/NativeMessagingHosts/         — NM manifest (Chromium)
#   ~/.config/BraveSoftware/Brave-Browser/...        — NM manifest (Brave)
#   ~/.config/microsoft-edge/NativeMessagingHosts/   — NM manifest (Edge)
#
set -euo pipefail

# ---------- Args ----------

EXTENSION_IDS=""
SKIP_DOWNLOAD=0

usage() {
    grep '^#' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --extension-id) EXTENSION_IDS="$2"; shift 2 ;;
        --skip-ytdlp-download) SKIP_DOWNLOAD=1; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$EXTENSION_IDS" ]; then
    echo "Error: --extension-id is required." >&2
    echo "Find it at chrome://extensions with Developer mode enabled." >&2
    exit 1
fi

# ---------- Paths ----------

INSTALL_DIR="$HOME/.local/share/PixelCatch"
HOST_NAME="com.pixelcatch.downloader"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Browsers we register with. Each entry is "label|nm-host-dir".
BROWSER_DIRS=(
    "Google Chrome|$HOME/.config/google-chrome/NativeMessagingHosts"
    "Chromium|$HOME/.config/chromium/NativeMessagingHosts"
    "Brave|$HOME/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts"
    "Microsoft Edge|$HOME/.config/microsoft-edge/NativeMessagingHosts"
    "Vivaldi|$HOME/.config/vivaldi/NativeMessagingHosts"
)

# ---------- Verify Python 3 ----------

PYTHON3="$(command -v python3 || true)"
if [ -z "$PYTHON3" ]; then
    echo "Error: python3 not found in PATH." >&2
    echo "Install it with one of:" >&2
    echo "    sudo apt install python3            (Debian/Ubuntu)" >&2
    echo "    sudo dnf install python3            (Fedora/RHEL)" >&2
    echo "    sudo pacman -S python               (Arch)" >&2
    exit 1
fi
echo "Python 3: $PYTHON3"

# ---------- Stage helper files ----------

mkdir -p "$INSTALL_DIR"
cp "$REPO_ROOT/native/downloader.py" "$INSTALL_DIR/downloader.py"
chmod 755 "$INSTALL_DIR/downloader.py"

# ---------- Get yt-dlp standalone binary ----------

YTDLP_BIN="$INSTALL_DIR/yt-dlp"
ARCH="$(uname -m)"
# Default to the portable Python zipapp — uses the user's system Python 3,
# which every distro ships, and avoids any cross-distro libc/libssl issues
# that occasionally bite the platform-specific PyInstaller bundles.
YTDLP_ASSET="yt-dlp"

if [ "$SKIP_DOWNLOAD" -eq 1 ]; then
    echo "Skipping yt-dlp download (--skip-ytdlp-download)."
elif [ -f "$YTDLP_BIN" ]; then
    echo "yt-dlp already present at $YTDLP_BIN — leaving it alone."
else
    URL="https://github.com/yt-dlp/yt-dlp/releases/latest/download/$YTDLP_ASSET"
    echo "Downloading yt-dlp ($YTDLP_ASSET)..."
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 -o "$YTDLP_BIN" "$URL"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$YTDLP_BIN" "$URL"
    else
        echo "Error: neither curl nor wget is installed; cannot download yt-dlp." >&2
        echo "Install one of them, or pass --skip-ytdlp-download and provide $YTDLP_BIN yourself." >&2
        exit 1
    fi
    chmod 755 "$YTDLP_BIN"
fi

# ---------- ffmpeg + ffprobe ----------
#
# Required for any 1080p/1440p/4K download (YouTube serves those as separate
# streams that yt-dlp must mux). If the system already has ffmpeg, prefer
# that — it's almost certainly more current than what we'd bundle. Otherwise
# pull a static build from johnvansickle.com (popular, signed, maintained).

if command -v ffmpeg >/dev/null 2>&1 && [ -z "${BUNDLE_FFMPEG:-}" ] && [ "$SKIP_DOWNLOAD" -eq 0 ]; then
    echo "ffmpeg already on PATH at $(command -v ffmpeg) — skipping bundle"
elif [ -f "$INSTALL_DIR/ffmpeg" ]; then
    echo "ffmpeg already bundled at $INSTALL_DIR/ffmpeg — leaving it alone"
elif [ "$SKIP_DOWNLOAD" -eq 1 ]; then
    echo "Skipping ffmpeg download (--skip-ytdlp-download). Provide ffmpeg + ffprobe in $INSTALL_DIR yourself, or have them on PATH."
else
    case "$ARCH" in
        x86_64|amd64) FFMPEG_TARBALL="ffmpeg-release-amd64-static.tar.xz" ;;
        aarch64|arm64) FFMPEG_TARBALL="ffmpeg-release-arm64-static.tar.xz" ;;
        armv7l|armhf) FFMPEG_TARBALL="ffmpeg-release-armhf-static.tar.xz" ;;
        *) FFMPEG_TARBALL="" ;;
    esac

    if [ -z "$FFMPEG_TARBALL" ]; then
        echo "Warning: no static ffmpeg build for arch '$ARCH'." >&2
        echo "Install ffmpeg via your package manager: sudo apt install ffmpeg / sudo dnf install ffmpeg / sudo pacman -S ffmpeg" >&2
    else
        URL="https://johnvansickle.com/ffmpeg/releases/$FFMPEG_TARBALL"
        TMP_TAR="$INSTALL_DIR/.ffmpeg.tar.xz"
        echo "Downloading ffmpeg ($FFMPEG_TARBALL)..."
        if command -v curl >/dev/null 2>&1; then
            curl -fL --retry 3 -o "$TMP_TAR" "$URL"
        else
            wget -q -O "$TMP_TAR" "$URL"
        fi
        # Tarball is a single top-level dir like ffmpeg-7.1.1-amd64-static/.
        # Extract just the ffmpeg + ffprobe binaries from it.
        tar -xJf "$TMP_TAR" -C "$INSTALL_DIR" --strip-components=1 \
            --wildcards '*/ffmpeg' '*/ffprobe' || {
                echo "Warning: ffmpeg extraction failed; downloads above 720p may not work." >&2
            }
        rm -f "$TMP_TAR"
        [ -f "$INSTALL_DIR/ffmpeg"  ] && chmod 755 "$INSTALL_DIR/ffmpeg"
        [ -f "$INSTALL_DIR/ffprobe" ] && chmod 755 "$INSTALL_DIR/ffprobe"
    fi
fi

# ---------- Generate runner.sh ----------

RUNNER="$INSTALL_DIR/runner.sh"
cat > "$RUNNER" <<RUNNER_EOF
#!/usr/bin/env bash
set -e
export PATH="$INSTALL_DIR:/usr/local/bin:/usr/bin:/bin:\${PATH:-}"
exec "$PYTHON3" "$INSTALL_DIR/downloader.py"
RUNNER_EOF
chmod 755 "$RUNNER"

# ---------- Build allowed_origins JSON ----------

ALLOWED_ORIGINS=""
IFS=',' read -ra IDS <<< "$EXTENSION_IDS"
for id in "${IDS[@]}"; do
    id="$(echo "$id" | xargs)"
    [ -z "$id" ] && continue
    if ! [[ "$id" =~ ^[a-p]{32}$ ]]; then
        echo "Warning: '$id' doesn't look like a valid Chrome extension ID." >&2
    fi
    [ -n "$ALLOWED_ORIGINS" ] && ALLOWED_ORIGINS+=$',\n    '
    ALLOWED_ORIGINS+="\"chrome-extension://$id/\""
done

if [ -z "$ALLOWED_ORIGINS" ]; then
    echo "Error: no valid extension IDs after parsing." >&2
    exit 1
fi

# ---------- Register with every detected browser ----------

REGISTERED=0
for entry in "${BROWSER_DIRS[@]}"; do
    label="${entry%%|*}"
    nmh_dir="${entry##*|}"
    parent="$(dirname "$nmh_dir")"

    # Only register if the browser's config dir already exists — otherwise
    # we'd be implying support for a browser the user doesn't even use.
    if [ ! -d "$parent" ]; then
        continue
    fi

    mkdir -p "$nmh_dir"
    cat > "$nmh_dir/$HOST_NAME.json" <<MANIFEST_EOF
{
  "name": "$HOST_NAME",
  "description": "PixelCatch YouTube downloader native host",
  "path": "$RUNNER",
  "type": "stdio",
  "allowed_origins": [
    $ALLOWED_ORIGINS
  ]
}
MANIFEST_EOF
    chmod 644 "$nmh_dir/$HOST_NAME.json"

    # Best-effort cleanup of legacy manifests from prior versions.
    rm -f "$nmh_dir/com.extension.ytdownloader.json" || true

    echo "Registered with: $label"
    REGISTERED=$((REGISTERED + 1))
done

if [ "$REGISTERED" -eq 0 ]; then
    echo
    echo "Warning: no Chromium-family browsers detected on this user account." >&2
    echo "PixelCatch was installed but isn't registered with any browser yet." >&2
    echo "Open Chrome (or Chromium / Brave / Edge) once to create its config dir, then re-run this script." >&2
    exit 1
fi

echo
echo "PixelCatch Helper installed."
echo "  Helper:    $INSTALL_DIR"
echo "  Logs:      \$XDG_STATE_HOME/pixelcatch/downloader.log (defaults to ~/.local/state/pixelcatch/)"
echo "  Browsers:  $REGISTERED registered"
echo
echo "Now reload PixelCatch in your browser's chrome://extensions page."
