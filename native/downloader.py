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
FORMAT_PRESETS = {
    # "best" / 2K / 4K — let yt-dlp pick the highest-quality stream.
    "best": (
        [
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba",
            "best",
        ],
        "res,tbr,fps,vcodec:av01,acodec:opus,lang,channels",
    ),
    "4k": (
        [
            "bv*[height<=2160]+ba[language~='^(en|eng)']",
            "bv*[height<=2160]+ba",
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba",
            "best",
        ],
        "res:2160,tbr,fps,vcodec:av01,acodec:opus,lang",
    ),
    "2k": (
        [
            "bv*[height<=1440]+ba[language~='^(en|eng)']",
            "bv*[height<=1440]+ba",
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba",
            "best",
        ],
        "res:1440,tbr,fps,vcodec:av01,acodec:opus,lang",
    ),
    # 1080p / 720p / 480p — prefer AVC (H.264) video + AAC (m4a) audio in
    # English (clean .mp4 output). On member-gate errors, drop the AVC and
    # m4a constraints in turn, finally dropping resolution.
    "1080p": (
        [
            "bv*[height<=1080][vcodec~='^(avc|h264)']+ba[ext=m4a][language~='^(en|eng)']",
            "bv*[height<=1080]+ba[language~='^(en|eng)']",
            "bv*[height<=1080]+ba",
            "best[height<=1080]",
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba",
            "best",
        ],
        "res:1080,tbr,fps,vcodec:avc1,acodec:m4a,lang",
    ),
    "720p": (
        [
            "bv*[height<=720][vcodec~='^(avc|h264)']+ba[ext=m4a][language~='^(en|eng)']",
            "bv*[height<=720]+ba[language~='^(en|eng)']",
            "bv*[height<=720]+ba",
            "best[height<=720]",
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba",
            "best",
        ],
        "res:720,tbr,fps,vcodec:avc1,acodec:m4a,lang",
    ),
    "480p": (
        [
            "bv*[height<=480][vcodec~='^(avc|h264)']+ba[ext=m4a][language~='^(en|eng)']",
            "bv*[height<=480]+ba[language~='^(en|eng)']",
            "bv*[height<=480]+ba",
            "best[height<=480]",
            "bv*+ba[language~='^(en|eng)']",
            "bv*+ba",
            "best",
        ],
        "res:480,tbr,fps,vcodec:avc1,acodec:m4a,lang",
    ),
    # Audio-only.
    "audio": (
        [
            "ba[ext=m4a][language~='^(en|eng)']",
            "ba[language~='^(en|eng)']",
            "ba[ext=m4a]",
            "ba",
            "best",
        ],
        "abr,acodec:m4a:opus,lang",
    ),
}

# Substrings in yt-dlp's error output that mean "this specific format is
# blocked but a different one might work" — triggers our chain fallback.
RETRYABLE_ERROR_PATTERNS = (
    "join this channel",
    "members-only",
    "members only",
    "available to this channel's members",
    "members get full access",
    "http error 403",
    "http error 401",
    "this video is private",
    "video unavailable",
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

        # Hint the popup that we're (re)connecting if we're retrying.
        if idx > 0:
            send_message({
                "type": "progress",
                "percent": 0,
                "filename": "Retrying with broader quality preset…",
                "speed": "",
                "eta": "",
                "status": (
                    f"Previous attempt blocked ({last_error[:120]}); "
                    f"trying fallback {attempt_label}."
                ),
            })

        result = _attempt_download(url, download_path, ytdlp, fmt, sort_order, format_key)

        if result["ok"]:
            # Success — emit the final done message and return.
            send_message({
                "type": "done",
                "filename": result["filename"] or "Complete",
                "message": (
                    f"Saved to {download_path}/{result['filename']}"
                    if result["filename"] else f"Download complete. Check {download_path}."
                ),
            })
            return

        last_error = result["error"]

        # Non-retryable errors stop the chain immediately (no point trying a
        # different format if cookies are missing, network is down, etc.).
        if not _is_retryable_error(last_error):
            break

    # All attempts failed — emit the last error.
    error_msg = last_error or "Download failed after exhausting all format fallbacks."
    if IS_MAC and "exited with code 255" in error_msg.lower():
        error_msg += (
            " On macOS this is usually Gatekeeper blocking yt-dlp's bundled"
            " Python. Fix: open Terminal and run "
            'sudo xattr -dr com.apple.quarantine "/Library/Application Support/PixelCatch"'
        )
    send_message({"type": "error", "error": error_msg})


def _attempt_download(url, download_path, ytdlp, fmt, sort_order, format_key):
    """One yt-dlp run. Returns {ok, filename, error}."""
    global _active_proc
    filename = ""
    audio_only = format_key in AUDIO_ONLY_FORMATS

    # --- yt-dlp invocation ---
    #
    # Cookies: --cookies-from-browser chrome reads the user's default Chrome
    # profile cookie database to extract YouTube auth (required for Premium
    # quality streams and members-only videos). yt-dlp parses the on-disk
    # cookie store but only sends youtube.com cookies in the actual HTTP
    # requests by virtue of standard cookie scoping.
    #
    # Player clients: order matters — yt-dlp merges format lists from all
    # listed clients, then the -f selector + -S sort pick the winner.
    #
    #   "tv"         — YouTube's TV interface. Critically, this client does
    #                  NOT require a PO (Proof-of-Origin) token, so it
    #                  exposes the full quality ladder including 4K / 8K.
    #                  Without `tv`, the `web` client alone caps you at 720p
    #                  on most videos in 2025+ because YouTube hides higher
    #                  formats from web clients without a PO token.
    #   "web"        — carries cookies (for Premium tier streams + members-
    #                  only content). Not enough on its own for 4K anymore.
    #   "web_safari" — exposes the Premium enhanced-bitrate 1080p stream.
    #   "mweb"       — surfaces additional AV1 variants on some videos.
    #
    # --no-playlist guards against the user pasting a /watch?...&list=...
    # URL and accidentally queueing 100 downloads.
    #
    # -S (sort) makes sure the highest-bitrate stream wins among the ones
    # that match -f — without it yt-dlp can pick a low-bitrate AV1 over a
    # higher-bitrate VP9 at the same resolution.
    cmd = [
        ytdlp,
        "--cookies-from-browser", "chrome",
        # `lang=en` makes the YouTube extractor request English metadata
        # (title, description) and prefer English-tagged audio tracks. Many
        # large channels now upload dubbed audio tracks in 5–10 languages;
        # without this hint yt-dlp picks whatever YouTube returns first,
        # which is often Spanish/Hindi/Portuguese instead of English.
        "--extractor-args", "youtube:player_client=tv,web,web_safari,mweb;lang=en",
        "--no-playlist",
        "-f", fmt,
        "-S", sort_order,
        "--newline",
        "--progress",
        "--progress-template",
        f"{PROGRESS_PREFIX}%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.speed)s|%(progress.eta)s",
        # Echo the actual format that won so a future "low quality" complaint
        # is debuggable from the log without re-running. Use the default
        # "video" event (fires once per video before download); `before_dl`
        # is NOT a valid --print event prefix and gets silently dropped.
        "--print", "[selected] %(format_id)s %(width)sx%(height)s @ %(tbr)skbps %(vcodec)s+%(acodec)s",
        "-o", os.path.join(download_path, "%(title)s.%(ext)s"),
    ]

    if not audio_only:
        # Prefer mp4 for compatibility, fall back to mkv when streams (4K VP9,
        # AV1, Opus, etc.) aren't mp4-compatible. Without the mkv fallback,
        # 4K downloads silently fail to merge.
        cmd += ["--merge-output-format", "mp4/mkv"]

    cmd.append(url)

    log(f"running: {cmd}")

    env = os.environ.copy()
    # Front-load OS-appropriate locations where the bundled or system yt-dlp
    # might live, so shutil.which / yt-dlp's own subprocess shells find it.
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

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,        # line-buffered so progress streams in real time
        env=env,
    )
    _active_proc = proc

    last_error = ""

    for line in proc.stdout:
        line = line.rstrip()
        log(f"yt-dlp: {line}")

        if line.startswith("ERROR:"):
            last_error = line.replace("ERROR:", "").strip()

        dest_m = DEST_RE.search(line)
        if dest_m:
            filename = os.path.basename(dest_m.group(1))

        merger_m = MERGER_RE.search(line)
        if merger_m:
            filename = os.path.basename(merger_m.group(1))
            send_message({
                "type": "progress",
                "percent": 99,
                "filename": filename,
                "speed": "",
                "eta": "",
                "status": "Merging formats…",
            })
            continue

        progress_event = parse_progress_line(line, filename)
        if progress_event:
            send_message(progress_event)

    proc.wait()
    _active_proc = None

    if proc.returncode == 0:
        return {"ok": True, "filename": filename, "error": ""}
    return {
        "ok": False,
        "filename": filename,
        "error": last_error or f"yt-dlp exited with code {proc.returncode}.",
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
