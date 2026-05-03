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

# Map UI symbol -> vetted yt-dlp -f selector.
#
# Notes for Premium / members-only content:
#   - YouTube serves 1440p/2160p as separate video+audio (VP9/AV1 + Opus/AAC),
#     and the highest-quality streams are often *not* mp4. We deliberately do
#     NOT lock the container to mp4 here — that would silently downgrade 4K to
#     1080p. The runner uses --merge-output-format mp4/mkv so the muxed file is
#     mp4 when possible and mkv when the codecs require it.
#   - "bv*" / "ba" allow yt-dlp to also pick a single combined stream when
#     that's actually the best option (some 360p/480p paths).
#   - Authentication for Premium / members-only happens via cookies; the format
#     string itself doesn't change — yt-dlp will simply have access to higher
#     bitrate streams when the cookies grant membership.
FORMAT_PRESETS = {
    "best":  "bv*+ba/best",
    "4k":    "bv*[height<=2160]+ba/best[height<=2160]",
    "2k":    "bv*[height<=1440]+ba/best[height<=1440]",
    "1080p": "bv*[height<=1080]+ba/best[height<=1080]",
    "720p":  "bv*[height<=720]+ba/best[height<=720]",
    "480p":  "bv*[height<=480]+ba/best[height<=480]",
    # Audio-only: prefer m4a so it plays everywhere; fall back to whatever
    # bestaudio resolves to (usually opus in webm).
    "audio": "ba[ext=m4a]/ba/best",
}

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

    Returns (url, format_string, download_path, format_key).
    `format_key` is the symbolic name (e.g. "4k", "audio") and lets the
    runner choose audio-only vs video+audio behavior.

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
    format_string = FORMAT_PRESETS[format_key]

    # Download path
    raw_path = msg.get("downloadPath", "")
    if not isinstance(raw_path, str):
        raise ValidationError("downloadPath must be a string.")
    raw_path = raw_path.strip()
    if not raw_path:
        download_path = DEFAULT_DOWNLOAD_DIR
    else:
        expanded = os.path.expanduser(raw_path)
        if not os.path.isabs(expanded):
            raise ValidationError("downloadPath must be absolute or start with ~.")
        if not _is_path_inside(expanded, ALLOWED_DOWNLOAD_DIRS):
            raise ValidationError(
                f"downloadPath {raw_path!r} is not inside an allowed directory."
            )
        if not os.path.isdir(expanded):
            raise ValidationError(f"downloadPath does not exist: {raw_path!r}")
        download_path = os.path.realpath(expanded)

    return url, format_string, download_path, format_key


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


def run_download(url, download_path, ytdlp, fmt, format_key):
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
    # Player client: "web" is the only client that consistently respects the
    # auth cookies, which is what unlocks membership-gated content and the
    # Premium-tier 1080p enhanced bitrate. Earlier configurations included
    # "ios" — that client ignores cookies and silently falls back to a
    # non-member view, defeating the whole point of this extension.
    cmd = [
        ytdlp,
        "--cookies-from-browser", "chrome",
        "--extractor-args", "youtube:player_client=web",
        "-f", fmt,
        "--newline",
        "--progress",
        "--progress-template",
        f"{PROGRESS_PREFIX}%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.speed)s|%(progress.eta)s",
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
        send_message({
            "type": "done",
            "filename": filename or "Complete",
            "message": (
                f"Saved to {download_path}/{filename}"
                if filename else f"Download complete. Check {download_path}."
            ),
        })
    else:
        error_msg = last_error or f"yt-dlp exited with code {proc.returncode}."

        # On macOS, exit 255 with no captured error is almost always Gatekeeper
        # killing the bundled Python.framework inside yt-dlp because the .pkg
        # was downloaded from the internet and its quarantine flag propagated
        # to the installed files. Surface the one-line fix instead of leaving
        # the user staring at a number.
        if (
            IS_MAC
            and proc.returncode == 255
            and not last_error
        ):
            error_msg += (
                " On macOS this is usually Gatekeeper blocking yt-dlp's bundled"
                " Python. Fix: open Terminal and run "
                'sudo xattr -dr com.apple.quarantine "/Library/Application Support/PixelCatch"'
            )

        send_message({"type": "error", "error": error_msg})


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
        url, fmt, download_path, format_key = validate_and_normalize_message(message)
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

    run_download(url, download_path, ytdlp, fmt, format_key)


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
