#!/usr/local/bin/python3
"""
Native messaging host — streams yt-dlp progress back to the extension.
Uses the long-lived connectNative port protocol (multiple messages).
"""

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import traceback

LOG = "/tmp/ytdownloader_native.log"


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


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


def find_ytdlp():
    found = shutil.which("yt-dlp")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp"):
        if os.path.isfile(candidate):
            return candidate
    return None


# [download]  42.3% of 123.45MiB at 1.23MiB/s ETA 00:42
PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+[\S]+\s+at\s+([\S]+)\s+ETA\s+([\S]+)"
)
# [download] Destination: foo.mp4
DEST_RE = re.compile(r"\[download\] Destination:\s+(.+)")
# [Merger] Merging formats into "foo.mp4"
MERGER_RE = re.compile(r'\[Merger\] Merging formats into "(.+)"')


def run_download(url, download_path, ytdlp, fmt="bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"):
    filename = ""

    cmd = [
        ytdlp,
        "--cookies-from-browser", "chrome",
        "--extractor-args", "youtube:player_client=ios,web",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--newline",
        "--progress",
        "-o", os.path.join(download_path, "%(title)s.%(ext)s"),
        url,
    ]

    log(f"running: {cmd}")

    env = os.environ.copy()
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:" + env.get("PATH", "")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

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
                "status": "Merging formats…"
            })
            continue

        prog_m = PROGRESS_RE.search(line)
        if prog_m:
            send_message({
                "type": "progress",
                "percent": float(prog_m.group(1)),
                "filename": filename or "Downloading…",
                "speed": prog_m.group(2),
                "eta": prog_m.group(3),
                "status": ""
            })

    proc.wait()
    if proc.returncode == 0:
        send_message({
            "type": "done",
            "filename": filename or "Complete",
            "message": (f"Saved to ~/Downloads/{filename}" if filename
                        else "Download complete. Check ~/Downloads.")
        })
    else:
        send_message({
            "type": "error",
            "error": last_error or f"yt-dlp exited with code {proc.returncode}."
        })


def main():
    log("--- native host started (connectNative port mode)")
    message = read_message()
    log(f"received: {message}")

    if message is None:
        send_message({"type": "error", "error": "No message received."})
        return

    if message.get("action") != "download":
        send_message({"type": "error", "error": f"Unknown action: {message.get('action')}"})
        return

    url = message.get("url", "").strip()
    if not url:
        send_message({"type": "error", "error": "No URL provided."})
        return

    download_path = os.path.expanduser(message.get("downloadPath", "~/Downloads"))
    fmt = message.get("format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best")

    ytdlp = find_ytdlp()
    log(f"yt-dlp: {ytdlp}")
    if not ytdlp:
        send_message({"type": "error", "error": "yt-dlp not found. Run: brew install yt-dlp"})
        return

    run_download(url, download_path, ytdlp, fmt)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log(f"UNCAUGHT: {traceback.format_exc()}")
        try:
            send_message({"type": "error", "error": "Native host crashed. Check /tmp/ytdownloader_native.log"})
        except Exception:
            pass
