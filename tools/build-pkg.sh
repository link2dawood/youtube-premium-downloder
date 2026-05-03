#!/bin/bash
#
# Build the PixelCatch Helper macOS .pkg installer.
#
# Usage:
#   ./tools/build-pkg.sh --extension-id <id>[,<id2>,...] [--version 1.0.0]
#
# Output:
#   dist/PixelCatch-Helper-<version>.pkg   (unsigned)
#
# To sign + notarize for distribution after building, see tools/README.md.
#
# REQUIREMENTS (developer's Mac):
#   - macOS with pkgbuild and productbuild (ship with Xcode CLT)
#   - curl (for downloading yt-dlp_macos on first build)
#   - Internet access on first run only — yt-dlp_macos is cached after that.
#
set -euo pipefail

# ---------- Args ----------

EXTENSION_IDS=""
VERSION=""

while [ $# -gt 0 ]; do
    case "$1" in
        --extension-id)
            EXTENSION_IDS="$2"; shift 2 ;;
        --version)
            VERSION="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1 ;;
    esac
done

if [ -z "$EXTENSION_IDS" ]; then
    echo "Error: --extension-id is required." >&2
    echo "Find your extension ID at chrome://extensions (Developer mode)." >&2
    echo "Multiple IDs allowed, comma-separated, e.g. --extension-id aaa...,bbb..." >&2
    exit 1
fi

# ---------- Paths ----------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Read version from manifest.json if --version not supplied.
if [ -z "$VERSION" ]; then
    VERSION="$(python3 -c "import json; print(json.load(open('$REPO_ROOT/manifest.json'))['version'])")"
fi

BUILD_DIR="$REPO_ROOT/build/pkg"
CACHE_DIR="$REPO_ROOT/build/cache"
DIST_DIR="$REPO_ROOT/dist"
ROOT_DIR="$BUILD_DIR/root/Library/Application Support/PixelCatch"
SCRIPTS_DIR="$BUILD_DIR/scripts"

PKG_TOOLS_DIR="$REPO_ROOT/tools/pkg"
POSTINSTALL_TEMPLATE="$PKG_TOOLS_DIR/scripts/postinstall.template"
DISTRIBUTION_TEMPLATE="$PKG_TOOLS_DIR/Distribution.xml"

# We deliberately use the yt-dlp zipapp (single-file Python script) here
# rather than the yt-dlp_macos PyInstaller binary. The PyInstaller bundle
# extracts its own Python.framework to a temp dir at runtime, and macOS 15
# (Sequoia) Gatekeeper independently verifies that extracted framework —
# which is unsigned/unnotarized, so it gets killed and the helper exits 255.
# The zipapp uses system Python (provided by Xcode CLT, which ships 3.9+ on
# every supported macOS) and skirts the whole problem.
YTDLP_URL="https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
YTDLP_CACHE="$CACHE_DIR/yt-dlp"

# ---------- Build allowed_origins JSON ----------

ALLOWED_ORIGINS=""
IFS=',' read -ra IDS <<< "$EXTENSION_IDS"
for id in "${IDS[@]}"; do
    id="$(echo "$id" | xargs)"  # trim
    [ -z "$id" ] && continue
    if ! [[ "$id" =~ ^[a-p]{32}$ ]]; then
        echo "Warning: '$id' does not look like a Chrome extension ID (32 lowercase a-p chars)." >&2
    fi
    if [ -n "$ALLOWED_ORIGINS" ]; then
        ALLOWED_ORIGINS+=",
    "
    fi
    ALLOWED_ORIGINS+="\"chrome-extension://$id/\""
done

if [ -z "$ALLOWED_ORIGINS" ]; then
    echo "Error: no valid extension IDs after parsing --extension-id." >&2
    exit 1
fi

echo "PixelCatch Helper build"
echo "  version:       $VERSION"
echo "  extension IDs: $EXTENSION_IDS"
echo

# ---------- Stage payload ----------

rm -rf "$BUILD_DIR"
mkdir -p "$ROOT_DIR" "$SCRIPTS_DIR" "$DIST_DIR" "$CACHE_DIR"

cp "$REPO_ROOT/native/downloader.py" "$ROOT_DIR/downloader.py"
chmod 755 "$ROOT_DIR/downloader.py"

# ---------- yt-dlp_macos (download + cache) ----------

if [ ! -f "$YTDLP_CACHE" ]; then
    echo "Downloading yt-dlp_macos (one-time, cached at $YTDLP_CACHE)..."
    curl -fL --retry 3 -o "$YTDLP_CACHE" "$YTDLP_URL"
fi

cp "$YTDLP_CACHE" "$ROOT_DIR/yt-dlp"
chmod 755 "$ROOT_DIR/yt-dlp"

# ---------- ffmpeg + ffprobe (macOS universal, signed) ----------
#
# yt-dlp needs ffmpeg to merge separate video+audio streams, which is
# every 1080p / 1440p / 4K download on YouTube. evermeet.cx publishes
# signed universal macOS builds — much easier than rolling our own.

FFMPEG_URL="https://evermeet.cx/ffmpeg/getrelease/zip"
FFPROBE_URL="https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"
FFMPEG_ZIP="$CACHE_DIR/ffmpeg-macos.zip"
FFPROBE_ZIP="$CACHE_DIR/ffprobe-macos.zip"

if [ ! -f "$FFMPEG_ZIP" ]; then
    echo "Downloading ffmpeg (macOS universal)..."
    curl -fL --retry 3 -o "$FFMPEG_ZIP" "$FFMPEG_URL"
fi
if [ ! -f "$FFPROBE_ZIP" ]; then
    echo "Downloading ffprobe (macOS universal)..."
    curl -fL --retry 3 -o "$FFPROBE_ZIP" "$FFPROBE_URL"
fi

unzip -p "$FFMPEG_ZIP"  ffmpeg  > "$ROOT_DIR/ffmpeg"
unzip -p "$FFPROBE_ZIP" ffprobe > "$ROOT_DIR/ffprobe"
chmod 755 "$ROOT_DIR/ffmpeg" "$ROOT_DIR/ffprobe"

# ---------- Generate postinstall ----------

# Pass substitution values via env vars and read them with $ENV{} on the perl
# side. This avoids any shell/regex interpretation of the values themselves
# (which contain JSON quotes, slashes, etc.).
ALLOWED_ORIGINS="$ALLOWED_ORIGINS" \
VERSION_VAL="$VERSION" \
    perl -pe '
        s/__ALLOWED_ORIGINS__/$ENV{ALLOWED_ORIGINS}/g;
        s/__VERSION__/$ENV{VERSION_VAL}/g;
    ' "$POSTINSTALL_TEMPLATE" > "$SCRIPTS_DIR/postinstall"
chmod 755 "$SCRIPTS_DIR/postinstall"

# Sanity-check the substitution worked.
if grep -q '__ALLOWED_ORIGINS__\|__VERSION__' "$SCRIPTS_DIR/postinstall"; then
    echo "Error: template placeholders left unfilled in postinstall." >&2
    exit 1
fi

# ---------- Generate Distribution.xml ----------

VERSION_VAL="$VERSION" \
    perl -pe 's/__VERSION__/$ENV{VERSION_VAL}/g;' "$DISTRIBUTION_TEMPLATE" \
    > "$BUILD_DIR/Distribution.xml"

# ---------- Build component pkg ----------

COMPONENT_PKG="$BUILD_DIR/component.pkg"

if ! command -v pkgbuild >/dev/null 2>&1; then
    echo
    echo "Note: pkgbuild not found on this machine — staging done at:"
    echo "  payload: $BUILD_DIR/root"
    echo "  scripts: $SCRIPTS_DIR"
    echo "Run this script on macOS (with Xcode CLT installed) to produce the .pkg."
    exit 0
fi

pkgbuild \
    --root "$BUILD_DIR/root" \
    --identifier "com.pixelcatch.helper" \
    --version "$VERSION" \
    --scripts "$SCRIPTS_DIR" \
    --install-location "/" \
    "$COMPONENT_PKG"

# ---------- Build product (final) pkg ----------

FINAL_PKG="$DIST_DIR/PixelCatch-Helper-$VERSION.pkg"

productbuild \
    --distribution "$BUILD_DIR/Distribution.xml" \
    --resources "$PKG_TOOLS_DIR/Resources" \
    --package-path "$BUILD_DIR" \
    "$FINAL_PKG"

# Also produce an unversioned alias so the popup's
# https://github.com/<repo>/releases/latest/download/PixelCatch-Helper.pkg
# URL hits the right asset on every release without needing per-version popup
# updates. Both filenames can be uploaded to the same GitHub release.
LATEST_PKG="$DIST_DIR/PixelCatch-Helper.pkg"
cp "$FINAL_PKG" "$LATEST_PKG"

echo
echo "Built:"
echo "  $FINAL_PKG  (versioned)"
echo "  $LATEST_PKG  (unversioned — what the popup downloads)"
echo
echo "To distribute outside your own Mac, sign and notarize:"
echo "  productsign --sign 'Developer ID Installer: <Your Name> (TEAMID)' \\"
echo "      \"$FINAL_PKG\" \"${FINAL_PKG%.pkg}-signed.pkg\""
echo "  xcrun notarytool submit \"${FINAL_PKG%.pkg}-signed.pkg\" \\"
echo "      --apple-id <your-apple-id> --team-id TEAMID --password <app-specific-pw> --wait"
echo "  xcrun stapler staple \"${FINAL_PKG%.pkg}-signed.pkg\""
