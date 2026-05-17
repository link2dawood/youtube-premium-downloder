#!/usr/bin/env python3
"""
Pipe a single native-messaging frame into the installed PixelCatch helper
and stream back its responses. Use to test the helper end-to-end from the
terminal, exactly the way Chrome's native-messaging port would talk to it.

Usage:
    python3 tools/_helper-bridge.py <URL> <preset> [save-location]

Exits 0 on success (helper emitted {"type":"done"}), 1 on error.
"""
import json
import os
import struct
import subprocess
import sys


HELPER_PATH = "/Library/Application Support/PixelCatch/downloader.py"


def frame(obj):
    body = json.dumps(obj).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def read_frame(stream):
    raw_len = stream.read(4)
    if len(raw_len) < 4:
        return None
    length = struct.unpack("<I", raw_len)[0]
    body = stream.read(length)
    return json.loads(body.decode("utf-8"))


def main():
    if len(sys.argv) < 3:
        print("Usage: _helper-bridge.py <URL> <preset> [save-location]", file=sys.stderr)
        sys.exit(2)

    url = sys.argv[1]
    preset = sys.argv[2]
    save_location = sys.argv[3] if len(sys.argv) > 3 else ""

    if not os.path.isfile(HELPER_PATH):
        print(f"Helper not found at {HELPER_PATH}", file=sys.stderr)
        sys.exit(1)

    msg = {"action": "download", "url": url, "format": preset}
    if save_location:
        msg["saveLocation"] = save_location

    proc = subprocess.Popen(
        [sys.executable, HELPER_PATH],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )

    try:
        proc.stdin.write(frame(msg))
        proc.stdin.flush()
    except BrokenPipeError:
        print("ERROR: helper closed stdin immediately.", file=sys.stderr)
        proc.kill()
        sys.exit(1)

    final_kind = None
    while True:
        try:
            event = read_frame(proc.stdout)
        except json.JSONDecodeError as e:
            print(f"ERROR: helper sent malformed frame: {e}", file=sys.stderr)
            break
        except Exception:
            break
        if event is None:
            break

        kind = event.get("type")
        if kind == "progress":
            pct = float(event.get("percent") or 0)
            name = event.get("filename") or "(no name)"
            speed = event.get("speed") or ""
            eta = event.get("eta") or ""
            status = event.get("status") or ""
            line = f"  [{pct:5.1f}%] {name}"
            if speed:
                line += f"  @ {speed}"
            if eta:
                line += f"  ETA {eta}"
            if status:
                line += f"  — {status}"
            print(line, flush=True)
        elif kind == "done":
            final_kind = "done"
            print()
            print(f"DONE: {event.get('message', 'download complete')}")
            print(f"       filename: {event.get('filename', '')}")
            break
        elif kind == "error":
            final_kind = "error"
            print()
            print(f"ERROR: {event.get('error', 'download failed')}", file=sys.stderr)
            break
        else:
            print(f"  ?  unknown event type: {event!r}")

    proc.wait(timeout=5)
    sys.exit(0 if final_kind == "done" else 1)


if __name__ == "__main__":
    main()
