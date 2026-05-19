#!/usr/bin/env python3
"""
PixelCatch native messaging host.

Purpose: download YouTube videos — including YouTube Premium quality streams
and channel members-only content — at any resolution up to 4K (2160p), or
audio-only. Authentication relies on the user's existing signed-in Chrome
session: yt-dlp reads YouTube cookies from the default Chrome profile so
gated content the user is authorized for is accessible.

Single-shot process: reads ONE JSON message from stdin (Chrome native messaging
frame: 4-byte little-endian length + UTF-8 body), validates it against an
allow-list, runs yt-dlp, streams progress events back on stdout, then exits.
Each download from the popup spawns a fresh instance.

Messages in (JSON):
    {"action": "ping"}                              -- liveness check from popup
    {
        "action": "download",
        "url": "https://www.youtube.com/watch?v=...",
        "format": "best" | "4k" | "2k" | "1080p" | "720p" | "480p" | "audio",
        "downloadPath": "~/Downloads"   # optional, must be inside an allowed dir
    }

Messages out (JSON, one per Chrome NM frame):
    {"type": "pong",     "version": "1.0.0", "platform": "darwin" | "linux" | "win32"}
    {"type": "progress", "percent": 42.3, "filename": "...", "speed": "1.2MiB/s", "eta": "00:42", "status": ""}
    {"type": "done",     "filename": "...", "message": "Saved to ~/Downloads/..."}
    {"type": "error",    "error": "..."}
"""

import json
import os
import platform
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
import traceback
import urllib.parse


# Bumped per release; surfaced in pong replies so the popup can detect a
# stale helper and prompt the user to upgrade.
HELPER_VERSION = "1.0.0"

# ---------- Platform helpers ----------

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def _platform_log_path():
    """OS-appropriate location for the host's log file."""
    if IS_MAC:
        return os.path.expanduser("~/Library/Logs/com.pixelcatch.downloader.log")
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "PixelCatch", "downloader.log")
    # Linux / *BSD: follow the XDG state spec.
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "pixelcatch", "downloader.log")


def _platform_default_download_dirs():
    """Whitelist of directories the host will write into."""
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Downloads"),
        os.path.join(home, "Videos"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
    ]
    if IS_MAC:
        candidates.append(os.path.join(home, "Movies"))
    # Resolve symlinks; skip dirs that don't exist on this machine.
    resolved = []
    for path in candidates:
        try:
            real = os.path.realpath(path)
            resolved.append(real)
        except OSError:
            continue
    # Dedupe while preserving order.
    seen = set()
    out = []
    for p in resolved:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


def _platform_save_locations():
    """
    Map of UI symbolic name -> absolute path for the popup's "Save to"
    dropdown. The popup sends the symbol; the helper resolves it here, so
    the path itself never crosses the messaging boundary (no chance of the
    popup smuggling a path the user didn't intend).
    """
    home = os.path.expanduser("~")
    mapping = {
        "downloads": os.path.join(home, "Downloads"),
        "videos":    os.path.join(home, "Videos"),
        "desktop":   os.path.join(home, "Desktop"),
        "documents": os.path.join(home, "Documents"),
    }
    if IS_MAC:
        # On macOS the iMovie/QuickTime/Photos default video bucket is Movies,
        # not Videos. Expose both — UI shows whichever exists.
        mapping["movies"] = os.path.join(home, "Movies")
    return mapping


# ---------- Logging ----------

LOG_PATH = _platform_log_path()


def _ensure_log_path():
    log_dir = os.path.dirname(LOG_PATH)
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        pass
    if not os.path.exists(LOG_PATH):
        try:
            # Touch with restrictive perms before any writes (POSIX only —
            # Windows ignores the mode argument, which is fine).
            fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(fd)
        except OSError:
            pass
    elif not IS_WINDOWS:
        try:
            os.chmod(LOG_PATH, 0o600)
        except OSError:
            pass


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# ---------- Allow-lists ----------

# Matched against the parsed URL hostname (after lowercasing).
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

# Map UI symbol -> (yt-dlp -f selector, yt-dlp -S sort criteria).
#
# Notes for Premium / members-only content:
#   - YouTube serves 1440p/2160p as separate video+audio (VP9/AV1 + Opus/AAC),
#     and the highest-quality streams are often *not* mp4. We deliberately do
#     NOT lock the container to mp4 here — that would silently downgrade 4K to
#     1080p. The runner uses --merge-output-format mp4/mkv so the muxed file is
#     mp4 when possible and mkv when the codecs require it.
#   - "bv*" / "ba" allow yt-dlp to also pick a single combined stream when
#     that's actually the best option (some 360p/480p paths).
#   - The -S (sort) criteria force the *highest-bitrate* stream that fits the
#     resolution cap. Without explicit sorting, yt-dlp's default order can
#     pick a low-bitrate AV1 over a much-higher-bitrate VP9 at the same
#     resolution — which is what users describe as "4K but looks like 1080p."
#   - "tbr" (total bitrate) is the strongest "actual quality" proxy. We weight
#     resolution > bitrate > fps > codec preference.
#   - Authentication for Premium / members-only happens via cookies; the format
#     string itself doesn't change — yt-dlp will simply have access to higher
#     bitrate streams when the cookies grant membership.
# Each preset is (fmt_chain, sort_str). fmt_chain is a LIST of yt-dlp format
# selectors tried in order — first one that downloads successfully wins. On
# members-only / 403 errors we drop to the next selector. This is critical
# because some videos have their ideal-quality combination gated behind
# channel membership while a lower-quality (or different-codec) version is
# publicly available — yt-dlp's manifest-time `/` fallback doesn't help
# because it picks on EXISTENCE, not on DOWNLOAD-ABILITY.
#
# IMPORTANT ORDERING RULE: exhaust ALL English-audio attempts (at every
# resolution) BEFORE accepting non-English audio. For many multi-language
# videos the English audio is gated to channel members but lower-quality
# English (or English at lower resolution) is public — we want that over
# a Spanish/Hindi/Ukrainian dub at the user's preferred resolution.
# EN-US STRICT — every chain step requires English-tagged audio (en, en-US,
# en-GB, eng) OR explicitly-undefined audio (und = no language tag set, which
# is what YouTube uses for the original audio on single-language English
# videos like Big Buck Bunny). NO fallback to "any audio" — if no English-
# tagged or untagged audio exists, the download fails with a clear error
# rather than silently delivering Spanish/Hindi/etc.
#
# Sort: `lang` FIRST so even when the no-filter fallback runs, yt-dlp
# prefers English-preferred-language formats over non-English by sort.
# `language_preference` is set high (10) by the YouTube extractor for
# formats matching the extractor's `lang=en` arg, default for others.
FORMAT_PRESETS = {
    "best": (
        [
            "bv*+ba[language~='^(en|eng)']",   # English-tagged
            "bv*+ba[language=und]",             # explicitly undefined
            "bv*+ba",                           # any (sort picks en-pref)
        ],
        "lang,res,tbr,fps,vcodec:av01,acodec:opus,channels",
    ),
    "4k": (
        [
            "bv*[height<=2160]+ba[language~='^(en|eng)']",
            "bv*[height<=2160]+ba[language=und]",
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba[language=und]",
            "bv*+ba",
        ],
        "lang,res:2160,tbr,fps,vcodec:av01,acodec:opus",
    ),
    "2k": (
        [
            "bv*[height<=1440]+ba[language~='^(en|eng)']",
            "bv*[height<=1440]+ba[language=und]",
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba[language=und]",
            "bv*+ba",
        ],
        "lang,res:1440,tbr,fps,vcodec:av01,acodec:opus",
    ),
    "1080p": (
        [
            # Preferred: 1080p AVC mp4 + English-tagged audio (clean en-US .mp4)
            "bv*[height<=1080][vcodec~='^(avc|h264)']+ba[ext=m4a][language~='^(en|eng)']",
            "bv*[height<=1080][vcodec~='^(avc|h264)']+ba[ext=m4a][language=und]",
            # Drop AVC constraint, keep English
            "bv*[height<=1080]+ba[language~='^(en|eng)']",
            "bv*[height<=1080]+ba[language=und]",
            # Drop resolution, keep English
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba[language=und]",
            # Last resort — no filter but sort puts English first
            "bv*+ba",
        ],
        "lang,res:1080,tbr,fps,vcodec:avc1,acodec:m4a",
    ),
    "720p": (
        [
            "bv*[height<=720][vcodec~='^(avc|h264)']+ba[ext=m4a][language~='^(en|eng)']",
            "bv*[height<=720][vcodec~='^(avc|h264)']+ba[ext=m4a][language=und]",
            "bv*[height<=720]+ba[language~='^(en|eng)']",
            "bv*[height<=720]+ba[language=und]",
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba[language=und]",
            "bv*+ba",
        ],
        "lang,res:720,tbr,fps,vcodec:avc1,acodec:m4a",
    ),
    "480p": (
        [
            "bv*[height<=480][vcodec~='^(avc|h264)']+ba[ext=m4a][language~='^(en|eng)']",
            "bv*[height<=480][vcodec~='^(avc|h264)']+ba[ext=m4a][language=und]",
            "bv*[height<=480]+ba[language~='^(en|eng)']",
            "bv*[height<=480]+ba[language=und]",
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba[language=und]",
            "bv*+ba",
        ],
        "lang,res:480,tbr,fps,vcodec:avc1,acodec:m4a",
    ),
    "audio": (
        [
            "ba[ext=m4a][language~='^(en|eng)']",
            "ba[language~='^(en|eng)']",
            "ba[ext=m4a][language=und]",
            "ba[language=und]",
            "ba",
        ],
        "lang,abr,acodec:m4a:opus",
    ),
}

# Substrings in yt-dlp's error output that mean "this specific format is
# blocked or unavailable, but a different one might work" — triggers our
# chain fallback to the next, broader selector.
RETRYABLE_ERROR_PATTERNS = (
    # Access-gated formats (membership / age / region / private).
    "join this channel",
    "members-only",
    "members only",
    "available to this channel's members",
    "members get full access",
    "http error 403",
    "http error 401",
    "this video is private",
    "video unavailable",

    # Selector-too-strict: yt-dlp couldn't find any format matching our
    # filter. The most common case: we required English audio via
    # [language^=en] but the video is single-language and has no language
    # tag at all. Falling back to a selector without the language filter
    # resolves it.
    "requested format is not available",
    "no video formats found",
    "no audio formats found",
    "no formats found",
)


def _is_retryable_error(text):
    if not text:
        return False
    lower = text.lower()
    return any(p in lower for p in RETRYABLE_ERROR_PATTERNS)

# Quality keys that download audio-only. The runner skips video merge args
# for these so yt-dlp doesn't try to merge a non-existent video stream.
AUDIO_ONLY_FORMATS = frozenset({"audio"})

# Whitelist of root directories the host will write to. Computed at import
# time but resolved per-OS so the same downloader.py works on macOS, Linux,
# and Windows installations.
ALLOWED_DOWNLOAD_DIRS = _platform_default_download_dirs() or (
    os.path.realpath(os.path.expanduser("~/Downloads")),
)
DEFAULT_DOWNLOAD_DIR = ALLOWED_DOWNLOAD_DIRS[0]

# Map of UI symbol -> path. Populated per-platform.
SAVE_LOCATIONS = _platform_save_locations()


# ---------- Native messaging frames ----------

def send_message(data):
    encoded = json.dumps(data).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) < 4:
        return None
    length = struct.unpack("<I", raw_length)[0]
    raw_body = sys.stdin.buffer.read(length)
    return json.loads(raw_body.decode("utf-8"))


# ---------- Validation ----------

class ValidationError(ValueError):
    """Raised when the incoming message fails the allow-list check."""


def _is_path_inside(child, parents):
    real_child = os.path.realpath(child)
    for parent in parents:
        try:
            common = os.path.commonpath([real_child, parent])
        except ValueError:
            continue
        if common == parent:
            return True
    return False


def validate_and_normalize_message(msg):
    """
    Validates a native-messaging payload from the extension popup.

    Returns (url, format_string, sort_string, download_path, format_key).
    `format_string` is the yt-dlp -f selector; `sort_string` is the matching
    -S sort order (which determines which stream wins among ones that match
    the selector). `format_key` is the symbolic name (e.g. "4k", "audio") and
    lets the runner choose audio-only vs video+audio behavior.

    Raises ValidationError on bad input.
    """
    if not isinstance(msg, dict):
        raise ValidationError("Message must be a JSON object.")

    action = msg.get("action")
    if action != "download":
        raise ValidationError(f"Unsupported action: {action!r}")

    # URL
    url = msg.get("url", "")
    if not isinstance(url, str):
        raise ValidationError("URL must be a string.")
    url = url.strip()
    if not url:
        raise ValidationError("Missing URL.")
    if len(url) > 2048:
        raise ValidationError("URL is too long.")

    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        raise ValidationError(f"Could not parse URL: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise ValidationError("Only http(s) URLs are allowed.")

    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise ValidationError(f"URL host {host!r} is not an allowed YouTube host.")

    # Format
    format_key = msg.get("format", "")
    if not isinstance(format_key, str):
        raise ValidationError("Format must be a string.")
    format_key = format_key.strip().lower()
    if format_key not in FORMAT_PRESETS:
        allowed = ", ".join(sorted(FORMAT_PRESETS))
        raise ValidationError(f"Unknown format {format_key!r}. Allowed: {allowed}.")
    format_chain, sort_string = FORMAT_PRESETS[format_key]

    # Download path: prefer the symbolic `saveLocation` (popup's "Save to"
    # dropdown). Fall back to the raw `downloadPath` for back-compat / when
    # the user types a custom path. Both are validated.
    save_location = msg.get("saveLocation", "")
    raw_path = msg.get("downloadPath", "")
    if not isinstance(save_location, str):
        raise ValidationError("saveLocation must be a string.")
    if not isinstance(raw_path, str):
        raise ValidationError("downloadPath must be a string.")
    save_location = save_location.strip().lower()
    raw_path = raw_path.strip()

    if save_location:
        if save_location not in SAVE_LOCATIONS:
            allowed = ", ".join(sorted(SAVE_LOCATIONS))
            raise ValidationError(
                f"Unknown saveLocation {save_location!r}. Allowed: {allowed}."
            )
        target = SAVE_LOCATIONS[save_location]
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            raise ValidationError(
                f"Could not create save location {target!r}: {exc}"
            ) from exc
        download_path = os.path.realpath(target)
    elif raw_path:
        expanded = os.path.expanduser(raw_path)
        if not os.path.isabs(expanded):
            raise ValidationError("downloadPath must be absolute or start with ~.")
        if not _is_path_inside(expanded, ALLOWED_DOWNLOAD_DIRS):
            allowed = ", ".join(ALLOWED_DOWNLOAD_DIRS)
            raise ValidationError(
                f"downloadPath {raw_path!r} is not inside an allowed directory. "
                f"Allowed roots: {allowed}."
            )
        if not os.path.isdir(expanded):
            raise ValidationError(f"downloadPath does not exist: {raw_path!r}")
        download_path = os.path.realpath(expanded)
    else:
        download_path = DEFAULT_DOWNLOAD_DIR

    return url, format_chain, sort_string, download_path, format_key


# ---------- yt-dlp discovery ----------

# ---------- Chrome profile auto-detection ----------
#
# yt-dlp's --cookies-from-browser chrome reads ONLY the Default profile.
# For multi-profile users, the membership account is often in a different
# profile, so YouTube returns "Join this channel" even though the user is a
# paying member.
#
# We enumerate every profile in ~/Library/Application Support/Google/Chrome/,
# try each as the cookie source, and use the first one whose cookies pass
# YouTube's auth. The result is cached per-channel so we don't probe again.

_PROFILE_CACHE_PATH = os.path.expanduser("~/Library/Caches/com.pixelcatch.downloader/profile-cache.json")


def _chrome_profiles_dir():
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if IS_LINUX:
        return os.path.expanduser("~/.config/google-chrome")
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        return os.path.join(local, "Google", "Chrome", "User Data")
    return ""


def _list_chrome_profiles():
    """Return list of profile directory names like ['Default', 'Profile 1', ...]."""
    chrome_dir = _chrome_profiles_dir()
    if not chrome_dir or not os.path.isdir(chrome_dir):
        return ["Default"]
    profiles = []
    for entry in sorted(os.listdir(chrome_dir)):
        if entry == "Default" or entry.startswith("Profile "):
            full = os.path.join(chrome_dir, entry)
            cookies_db = os.path.join(full, "Cookies")
            cookies_db_alt = os.path.join(full, "Network", "Cookies")
            if os.path.isfile(cookies_db) or os.path.isfile(cookies_db_alt):
                profiles.append(entry)
    # Default should always be first
    if "Default" in profiles:
        profiles.remove("Default")
        profiles.insert(0, "Default")
    return profiles or ["Default"]


def _load_profile_cache():
    try:
        with open(_PROFILE_CACHE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_profile_cache(cache):
    try:
        os.makedirs(os.path.dirname(_PROFILE_CACHE_PATH), exist_ok=True)
        with open(_PROFILE_CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except OSError:
        pass


def _channel_id_from_url(url):
    """Best-effort channel identifier from a video URL (used as cache key)."""
    try:
        parsed = urllib.parse.urlparse(url)
        # Use just the path + video ID as the cache key — different videos
        # from the same channel typically share a profile that works.
        return parsed.path + "?" + urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    except Exception:
        return url


def _probe_profile(ytdlp, url, profile):
    """Quick simulate-only probe: does this profile's cookies let yt-dlp
    extract this video's manifest without a members-only error? Returns
    True/False. Bounded to ~15s."""
    try:
        result = subprocess.run(
            [
                ytdlp,
                "--cookies-from-browser", f"chrome:{profile}",
                "--extractor-args", "youtube:player_client=web",
                "--no-playlist",
                "--simulate",
                "--quiet",
                "--no-warnings",
                "-f", "ba",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return True
        # Look for members-only / auth errors in stderr
        err = (result.stderr + result.stdout).lower()
        if "join this channel" in err or "members-only" in err or "403" in err:
            return False
        # Other errors (format not available, network) — treat as inconclusive
        # but DON'T pick this profile, try the next.
        return False
    except (subprocess.TimeoutExpired, OSError):
        return False


_PROFILE_PROBED_THIS_RUN = {}  # url -> profile, in-process cache


def detect_chrome_profile(ytdlp, url):
    """
    Find a Chrome profile whose cookies authenticate to this URL. Returns the
    profile name (e.g. "Default", "Profile 3"). Caches per channel-key so we
    only probe once per channel.

    Skips the probe entirely and returns "Default" if we already probed this
    URL during the current process (so a multi-attempt format chain doesn't
    re-probe N times).
    """
    if url in _PROFILE_PROBED_THIS_RUN:
        return _PROFILE_PROBED_THIS_RUN[url]

    cache = _load_profile_cache()
    key = _channel_id_from_url(url)

    # Use cached result if it's recent (< 7 days).
    cached = cache.get(key)
    import time as _time
    now = _time.time()
    if cached and (now - cached.get("ts", 0)) < 7 * 86400:
        log(f"Using cached profile for {key}: {cached['profile']}")
        _PROFILE_PROBED_THIS_RUN[url] = cached["profile"]
        return cached["profile"]

    profiles = _list_chrome_profiles()
    log(f"Probing {len(profiles)} Chrome profiles for working cookies: {profiles}")

    for profile in profiles:
        log(f"  trying profile: {profile}")
        if _probe_profile(ytdlp, url, profile):
            log(f"  ✓ profile {profile!r} works — caching")
            cache[key] = {"profile": profile, "ts": now}
            _save_profile_cache(cache)
            _PROFILE_PROBED_THIS_RUN[url] = profile
            return profile

    log(f"  no profile worked; falling back to Default (probably tier-gated)")
    _PROFILE_PROBED_THIS_RUN[url] = "Default"
    return "Default"


def find_ytdlp():
    """
    Locate the yt-dlp binary. Looks in PATH first (which is set by the
    installer's runner so the bundled yt-dlp wins), then falls back to
    well-known per-OS install locations.
    """
    # shutil.which honors PATHEXT on Windows, so it'll pick up yt-dlp.exe.
    found = shutil.which("yt-dlp")
    if found:
        return found

    if IS_WINDOWS:
        candidates = [
            os.path.join(
                os.environ.get("PROGRAMFILES", "C:\\Program Files"),
                "PixelCatch",
                "yt-dlp.exe",
            ),
            os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")),
                "PixelCatch",
                "yt-dlp.exe",
            ),
        ]
    elif IS_MAC:
        candidates = [
            "/Library/Application Support/PixelCatch/yt-dlp",
            "/opt/homebrew/bin/yt-dlp",
            "/usr/local/bin/yt-dlp",
        ]
    else:
        candidates = [
            os.path.expanduser("~/.local/share/PixelCatch/yt-dlp"),
            "/usr/local/bin/yt-dlp",
            "/usr/bin/yt-dlp",
        ]

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


# ---------- Progress parsing ----------

# Stable, machine-readable progress lines emitted by --progress-template:
#   "PCPROGRESS|<downloaded>|<total>|<speed>|<eta>"
# (Fields can be "NA" if yt-dlp doesn't know yet.)
PROGRESS_PREFIX = "PCPROGRESS|"

# Fallback for legacy / unmatched lines.
LEGACY_PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+\S+\s+at\s+(\S+)\s+ETA\s+(\S+)"
)
DEST_RE = re.compile(r"\[download\] Destination:\s+(.+)")
MERGER_RE = re.compile(r'\[Merger\] Merging formats into "(.+)"')


def _safe_float(value, default=None):
    if value in (None, "", "NA", "N/A"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_bytes_per_sec(value):
    bps = _safe_float(value)
    if bps is None or bps <= 0:
        return ""
    units = ("B/s", "KiB/s", "MiB/s", "GiB/s")
    idx = 0
    while bps >= 1024 and idx < len(units) - 1:
        bps /= 1024.0
        idx += 1
    return f"{bps:.1f}{units[idx]}"


def _format_eta(value):
    seconds = _safe_float(value)
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_progress_line(line, current_filename):
    """
    Returns a dict suitable for send_message(...) or None if the line is not
    a progress event.
    """
    if line.startswith(PROGRESS_PREFIX):
        parts = line[len(PROGRESS_PREFIX):].split("|")
        # parts: [downloaded, total, speed, eta]
        downloaded = _safe_float(parts[0] if len(parts) > 0 else None)
        total = _safe_float(parts[1] if len(parts) > 1 else None)
        speed = parts[2] if len(parts) > 2 else ""
        eta = parts[3] if len(parts) > 3 else ""
        percent = 0.0
        if downloaded is not None and total and total > 0:
            percent = max(0.0, min(100.0, (downloaded / total) * 100.0))
        return {
            "type": "progress",
            "percent": round(percent, 1),
            "filename": current_filename or "Downloading…",
            "speed": _format_bytes_per_sec(speed),
            "eta": _format_eta(eta),
            "status": "",
        }

    legacy = LEGACY_PROGRESS_RE.search(line)
    if legacy:
        return {
            "type": "progress",
            "percent": float(legacy.group(1)),
            "filename": current_filename or "Downloading…",
            "speed": legacy.group(2),
            "eta": legacy.group(3),
            "status": "",
        }

    return None


# ---------- Download ----------

_active_proc = None


def _terminate_active(_sig=None, _frame=None):
    global _active_proc
    proc = _active_proc
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    sys.exit(0)


def run_download(url, download_path, ytdlp, fmt_chain, sort_order, format_key):
    """
    Try each format selector in fmt_chain in order. The first one that
    downloads successfully wins. On members-only / 403 errors we try the
    next; on any other error we stop immediately (those aren't fixable by
    picking a different format). Sends ONE final done/error message to the
    popup regardless of how many attempts ran.
    """
    # Back-compat: if a caller passes a single string, wrap it in a list.
    if isinstance(fmt_chain, str):
        fmt_chain = [fmt_chain]

    last_error = ""
    for idx, fmt in enumerate(fmt_chain):
        attempt_label = f"attempt {idx + 1}/{len(fmt_chain)}"
        log(f"--- {attempt_label} with -f {fmt[:120]}")

        # Hint the popup that we're (re)connecting if we're retrying. Keep
        # the message short — the popup shows this in the progress card and
        # repeating the full error every time looks like the same failure
        # firing over and over.
        if idx > 0:
            send_message({
                "type": "progress",
                "percent": 0,
                "filename": f"Trying alternative format ({attempt_label})…",
                "speed": "",
                "eta": "",
                "status": "Previous quality wasn't available — trying a broader option.",
            })

        result = _attempt_download(url, download_path, ytdlp, fmt, sort_order, format_key)

        if result["ok"]:
            # Success — emit the final done message using the canonical
            # filepath that yt-dlp reported via --print after_move.
            filepath = result.get("filepath", "")
            filename = result.get("filename", "")
            if result.get("already_exists"):
                send_message({
                    "type": "done",
                    "filename": filename,
                    "filepath": filepath,
                    "message": (
                        f"Already downloaded: {filepath}. "
                        "Delete the existing file if you want to re-download "
                        "(e.g. with different quality settings)."
                    ),
                })
            else:
                send_message({
                    "type": "done",
                    "filename": filename,
                    "filepath": filepath,
                    "message": f"Saved to {filepath}" if filepath
                               else f"Download complete. Check {download_path}.",
                })
            return

        last_error = result["error"]

        # Non-retryable errors stop the chain immediately (no point trying a
        # different format if cookies are missing, network is down, etc.).
        if not _is_retryable_error(last_error):
            break

    # All attempts failed — emit a focused, actionable error.
    error_msg = last_error or "Download failed after exhausting all format fallbacks."

    if _is_retryable_error(error_msg):
        # Try to extract the exact tier name from YouTube's error so the user
        # knows EXACTLY what membership level is needed. Examples of the raw
        # error format:
        #   "available to this channel's members on level: Full Interviews Early"
        #   "available on level: Inner Circle (or any higher level)"
        tier_match = re.search(
            r"members?\s+on\s+level:\s*([^.\n]+?)(?:\s*\(or|\.|$)",
            error_msg,
            re.IGNORECASE,
        )
        if tier_match:
            tier = tier_match.group(1).strip().rstrip(".)")
            error_msg = (
                f"This video requires the '{tier}' membership tier "
                f"(or higher). Your current membership doesn't include this "
                f"tier, so YouTube blocks the download. To watch it: upgrade "
                f"your channel membership to '{tier}' or above on YouTube."
            )
        else:
            error_msg = (
                "This video is members-only. Every quality option we tried "
                f"({len(fmt_chain)} attempts including 4K/1080p/720p/audio-only) "
                "was blocked by YouTube. Either you're not a paying member of this "
                "channel, or you're a member with a DIFFERENT Google account than "
                "the one currently active in Chrome. Open the video URL in Chrome — "
                "if it plays, switch accounts via the popup's Switch Account button. "
                "If you see a 'Join channel' button instead, you'd need to actually join."
            )

    if IS_MAC and "exited with code 255" in error_msg.lower():
        error_msg += (
            " On macOS this is usually Gatekeeper blocking yt-dlp's bundled"
            " Python. Fix: open Terminal and run "
            'sudo xattr -dr com.apple.quarantine "/Library/Application Support/PixelCatch"'
        )
    send_message({"type": "error", "error": error_msg})


# Marker used to tag the structured filepath line in yt-dlp's stdout. yt-dlp
# emits this line via `--print after_move:PCFILE=%(filepath)s` AFTER the file
# has been moved to its final post-merge location, so the path it reports is
# the actual on-disk path — not a guessed name from log parsing.
FILEPATH_MARKER = "PCFILE="

# Hard cap on how long a single yt-dlp invocation may run. Beyond this the
# helper terminates the process and returns a structured timeout error
# rather than letting it run forever (and pinning the popup's port open).
DOWNLOAD_TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def _runner_env():
    """Subprocess environment with PixelCatch's install dirs on PATH so
    yt-dlp's spawned ffmpeg/ffprobe children find the bundled binaries."""
    env = os.environ.copy()
    if IS_WINDOWS:
        extra_path = os.pathsep.join([
            os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "PixelCatch"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "PixelCatch"),
        ])
    else:
        extra_path = os.pathsep.join([
            "/Library/Application Support/PixelCatch",        # macOS .pkg install dir
            os.path.expanduser("~/.local/share/PixelCatch"),   # Linux per-user install dir
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/usr/bin",
            "/bin",
        ])
    env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
    return env


def _attempt_download(url, download_path, ytdlp, fmt, sort_order, format_key):
    """
    Run yt-dlp once for `url`. Returns a structured dict:
        {ok: bool, filepath: str, filename: str, error: str, already_exists: bool}

    Design principles (vs. the old log-scraping approach):

    * The canonical filepath comes from yt-dlp's `--print after_move:` event,
      tagged with our `FILEPATH_MARKER` so we can extract it from mixed
      stdout without regex-matching "[download] Destination:" lines or other
      human-readable log noise.
    * stdout and stderr are read on dedicated threads so neither can block
      the other (yt-dlp writes a lot to both, especially during merge).
    * Subprocess has a hard timeout via Popen.wait(timeout=...). On timeout
      we send SIGTERM, then SIGKILL if it doesn't exit, then surface the
      situation as a clean error to the caller — no zombie processes.
    * Audio is force-tagged `language=eng` via ffmpeg metadata so the merged
      mp4 reports `TAG:language=eng` to ffprobe / players, instead of `und`
      which has been confusing users for weeks.
    * Output container is `mp4` only (not `mp4/mkv`) — the brief calls for
      mp4. yt-dlp will pick mp4-compatible streams when possible; if the
      selected formats genuinely can't fit mp4 (e.g. VP9 4K + Opus), yt-dlp
      emits a clear error that the retry chain in run_download() handles.
    """
    global _active_proc
    audio_only = format_key in AUDIO_ONLY_FORMATS

    # Auto-detect Chrome profile (multi-profile users — see detect_chrome_profile).
    chrome_profile = detect_chrome_profile(ytdlp, url)

    # Build the yt-dlp argv. Comments inline reference the engineering brief's
    # required flags so anyone editing this can trace back to "why".
    cmd = [
        ytdlp,
        # --- Auth / extractor knobs ---
        "--cookies-from-browser", f"chrome:{chrome_profile}",
        "--extractor-args", "youtube:player_client=tv,web,web_safari,mweb;lang=en",
        "--no-playlist",

        # --- Format selection ---
        "-f", fmt,
        "-S", sort_order,

        # --- Progress output for the popup (machine-readable, line-prefixed) ---
        "--newline",
        "--progress",
        "--progress-template",
        f"{PROGRESS_PREFIX}%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.speed)s|%(progress.eta)s",

        # --- THE structured filepath line. `after_move` fires only once the
        #     file is at its final post-process+move location, so the path is
        #     authoritative. The PCFILE= marker lets our parser pick it out
        #     unambiguously from mixed stdout. ---
        "--print", f"after_move:{FILEPATH_MARKER}%(filepath)s",

        # --- Output template ---
        "-o", os.path.join(download_path, "%(title)s.%(ext)s"),
    ]

    if not audio_only:
        cmd += [
            # mp4 only per brief. If selected streams can't fit (rare —
            # VP9/AV1 4K with Opus), yt-dlp errors out, the chain in
            # run_download() falls through to a more permissive selector.
            "--merge-output-format", "mp4",
            # Force the audio stream's language metadata tag to `eng` so the
            # final mp4 reports `TAG:language=eng` instead of `und`. The
            # `ffmpeg:` prefix scopes the args to the ffmpeg merge step.
            "--postprocessor-args", "ffmpeg:-metadata:s:a:0 language=eng",
        ]
    else:
        # Audio-only: tag the language too so the resulting m4a is labelled.
        cmd += [
            "--postprocessor-args", "ffmpeg:-metadata:s:a:0 language=eng",
        ]

    cmd.append(url)
    log(f"running: {cmd}")

    # Subprocess: stdout and stderr on separate pipes (no `stderr=STDOUT`
    # merging). yt-dlp writes a lot during merge; we don't want one stream
    # to back up and block the other.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=_runner_env(),
    )
    _active_proc = proc

    # Shared state populated by the stream-reader threads.
    state = {
        "filepath": "",            # set when PCFILE=... line arrives
        "already_exists_path": "",  # set on "[download] X has already been downloaded"
        "last_error": "",
        "current_filename": "",    # for progress messages while download is running
    }
    state_lock = threading.Lock()

    def _read_stdout():
        for raw in proc.stdout:
            line = raw.rstrip()
            log(f"yt-dlp[out]: {line}")

            # 1. Structured filepath marker (canonical — overrides everything).
            if line.startswith(FILEPATH_MARKER):
                with state_lock:
                    state["filepath"] = line[len(FILEPATH_MARKER):].strip()
                continue

            # 2. Progress: PCPROGRESS|... → parse and forward.
            if line.startswith(PROGRESS_PREFIX) or LEGACY_PROGRESS_RE.search(line):
                with state_lock:
                    cur_name = state["current_filename"]
                evt = parse_progress_line(line, cur_name)
                if evt:
                    send_message(evt)
                continue

            # 3. Filename hints (NOT used as final truth — only to label
            #    in-progress messages before the after_move event fires).
            dest_m = DEST_RE.search(line)
            if dest_m:
                # Strip yt-dlp's intermediate `.fNNN.ext` suffix that
                # appears on per-stream destinations during DASH merges.
                raw_name = os.path.basename(dest_m.group(1))
                cleaned = re.sub(r"\.f\d+(?=\.[^.]+$)", "", raw_name)
                with state_lock:
                    state["current_filename"] = cleaned
                continue
            merger_m = MERGER_RE.search(line)
            if merger_m:
                with state_lock:
                    state["current_filename"] = os.path.basename(merger_m.group(1))
                send_message({
                    "type": "progress",
                    "percent": 99,
                    "filename": state["current_filename"],
                    "speed": "",
                    "eta": "",
                    "status": "Merging formats…",
                })
                continue

            # 4. Already-exists no-op detection (yt-dlp's --no-overwrites default).
            if "has already been downloaded" in line:
                m = re.search(r"\[download\]\s+(.+?)\s+has already been downloaded", line)
                if m:
                    with state_lock:
                        state["already_exists_path"] = m.group(1).strip()
                continue

            # 5. ERROR lines (yt-dlp prints these to stdout sometimes too).
            if line.startswith("ERROR:"):
                with state_lock:
                    state["last_error"] = line.replace("ERROR:", "").strip()
                continue

            # Anything else: just log, ignore for parsing purposes.
        try:
            proc.stdout.close()
        except Exception:
            pass

    def _read_stderr():
        for raw in proc.stderr:
            line = raw.rstrip()
            log(f"yt-dlp[err]: {line}")
            if line.startswith("ERROR:"):
                with state_lock:
                    state["last_error"] = line.replace("ERROR:", "").strip()
        try:
            proc.stderr.close()
        except Exception:
            pass

    t_out = threading.Thread(target=_read_stdout, daemon=True)
    t_err = threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    # Bounded wait. On timeout, kill the process and surface a clean error.
    try:
        rc = proc.wait(timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log(f"yt-dlp timed out after {DOWNLOAD_TIMEOUT_SECONDS}s — terminating")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        except Exception:
            pass
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        _active_proc = None
        return {
            "ok": False,
            "filepath": "",
            "filename": "",
            "error": (
                f"Download exceeded the {DOWNLOAD_TIMEOUT_SECONDS // 60}-minute"
                " timeout and was terminated. Slow connection, or YouTube"
                " is throttling — try again or pick a lower quality."
            ),
            "already_exists": False,
        }

    t_out.join(timeout=5)
    t_err.join(timeout=5)
    _active_proc = None

    # Snapshot state under lock (threads have exited at this point but be safe).
    with state_lock:
        filepath = state["filepath"]
        already_exists_path = state["already_exists_path"]
        last_error = state["last_error"]

    # === Success paths ===
    if rc == 0:
        if filepath:
            return {
                "ok": True,
                "filepath": filepath,
                "filename": os.path.basename(filepath),
                "error": "",
                "already_exists": False,
            }
        if already_exists_path:
            return {
                "ok": True,
                "filepath": already_exists_path,
                "filename": os.path.basename(already_exists_path),
                "error": "",
                "already_exists": True,
            }
        # Exit 0 but no structured filepath — defensive. Shouldn't happen
        # with --print after_move, but if it does, we don't fabricate a name.
        return {
            "ok": False,
            "filepath": "",
            "filename": "",
            "error": (
                "yt-dlp exited successfully but didn't report a final filepath. "
                "Check ~/Library/Logs/com.pixelcatch.downloader.log for details."
            ),
            "already_exists": False,
        }

    # === Failure paths ===
    return {
        "ok": False,
        "filepath": "",
        "filename": "",
        "error": last_error or f"yt-dlp exited with code {rc}.",
        "already_exists": False,
    }


# ---------- Entry point ----------

def main():
    _ensure_log_path()
    log("--- native host started")

    signal.signal(signal.SIGTERM, _terminate_active)
    try:
        signal.signal(signal.SIGINT, _terminate_active)
    except (ValueError, OSError):
        pass

    message = read_message()
    log(f"received: {message}")

    if message is None:
        send_message({"type": "error", "error": "No message received."})
        return

    # Lightweight liveness check from the popup — answer with pong + version,
    # then exit. No validation needed; ping carries no user input.
    if isinstance(message, dict) and message.get("action") == "ping":
        send_message({
            "type": "pong",
            "version": HELPER_VERSION,
            "platform": sys.platform,
        })
        return

    try:
        url, fmt_chain, sort_order, download_path, format_key = validate_and_normalize_message(message)
    except ValidationError as exc:
        send_message({"type": "error", "error": str(exc)})
        return

    ytdlp = find_ytdlp()
    log(f"yt-dlp: {ytdlp}")
    if not ytdlp:
        if IS_MAC:
            hint = "Reinstall the PixelCatch Helper .pkg, or `brew install yt-dlp`."
        elif IS_WINDOWS:
            hint = "Reinstall the PixelCatch Helper from PixelCatch-Helper-Setup.exe."
        else:
            hint = "Re-run tools/install-linux.sh, or `pip install --user yt-dlp`."
        send_message({
            "type": "error",
            "error": f"yt-dlp not found. {hint}",
        })
        return

    run_download(url, download_path, ytdlp, fmt_chain, sort_order, format_key)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log(f"UNCAUGHT: {traceback.format_exc()}")
        try:
            send_message({
                "type": "error",
                "error": "Native host crashed. Check ~/Library/Logs/com.pixelcatch.downloader.log",
            })
        except Exception:
            pass
