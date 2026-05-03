# PixelCatch

A Chrome extension purpose-built for downloading **YouTube Premium quality streams and channel members-only videos**, at any resolution from 480p up to **4K (2160p)**, or audio-only. All processing is local — there's no remote server, no analytics, no Google account integration. Authentication happens through the YouTube cookies in your existing signed-in Chrome session.

## What this is for

The use cases this extension is designed around:

- Downloading videos from channels you're a paying member of (members-only / "join" content)
- Downloading at the **Premium-tier 1080p enhanced bitrate** when you have a YouTube Premium subscription
- Pulling 4K (2160p) and 2K (1440p) streams in their original VP9/AV1 quality
- Saving audio-only m4a tracks for music or podcasts

If you don't have an active membership / Premium subscription, the extension will still download whatever the public can see — it just won't unlock the gated content.

## Quality presets

| Preset | What it picks |
|---|---|
| **Best available** | The highest-quality video + audio your account has access to. Picks Premium streams when present. |
| **4K (2160p)** | Up to 2160p, typically VP9 or AV1 in webm; merged to mkv if mp4 isn't possible. |
| **2K (1440p)** | Up to 1440p. |
| **1080p (Full HD)** | Up to 1080p. With Premium, picks the enhanced-bitrate 1080p stream when available. |
| **720p (HD)** | Up to 720p. |
| **480p (SD)** | Up to 480p. |
| **Audio only** | Bestaudio, prefers m4a; falls back to whatever container yt-dlp picks. |

The extension never locks the container to mp4 for video presets, because YouTube doesn't ship 4K/1440p as mp4 — that constraint would silently downgrade your download to 1080p. Output is mp4 when codecs allow, mkv otherwise.

## How it works

```
┌──────────────┐    connectNative     ┌──────────────────────┐
│ popup/popup.js │ ───────────────────▶ │ native/downloader.py │ ──▶ yt-dlp ──▶ youtube.com
└──────────────┘   stdio (NM frames)  └──────────────────────┘     ▲
       ▲                                          │                │
       │       progress / done / error            │      Chrome cookies
       └──────────────────────────────────────────┘    (your YouTube session)
```

1. The popup validates the YouTube URL and the quality preset locally.
2. It opens a Chrome native messaging port to `com.pixelcatch.downloader`.
3. The Python host re-validates everything against an allow-list, then runs `yt-dlp` and streams progress events back to the popup.
4. `yt-dlp` reads your YouTube cookies from your default Chrome profile so members-only / Premium content is accessible.
5. The host writes the file to `~/Downloads` (configurable to `~/Movies` or `~/Videos` via the message payload — anything else is rejected).

## Install

PixelCatch runs on **macOS, Windows, and Linux**. Chrome, Chromium, Brave, Microsoft Edge, and Vivaldi are all supported.

### Step 1 — load the extension (same on every OS)

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** and select this folder
4. Copy the extension ID Chrome assigns

### Step 2 — install the helper

The Chrome extension talks to a small local helper that runs `yt-dlp`. Pick the installer for your operating system:

#### macOS

Double-click `dist/PixelCatch-Helper-<version>.pkg`. Enter your admin password. Done.

You only need to **build** the `.pkg` once on your own machine:
```bash
./tools/build-pkg.sh --extension-id <your-extension-id>
```
After that the same `.pkg` works for any user you share it with.

#### Windows

Double-click `dist\PixelCatch-Helper-Setup-<version>.exe`. Click through the wizard. Done.

To **build** the `.exe`, on a Windows machine with [Inno Setup 6](https://jrsoftware.org/isdl.php) installed:
```powershell
.\tools\build-win.ps1 -ExtensionId <your-extension-id>
```
The installer bundles a portable Python and `yt-dlp.exe` — users don't need to install anything else.

Power users can skip the `.exe` and run a PowerShell installer instead:
```powershell
.\tools\install-windows.ps1 -ExtensionId <your-extension-id>
```

#### Linux

```bash
./tools/install-linux.sh --extension-id <your-extension-id>
```

Per-user install (no `sudo`). Auto-detects every Chromium-family browser you have installed and registers with each one. Downloads the right `yt-dlp` binary for your architecture.

Requires Python 3 in PATH (every modern distro has it).

### Step 3 — reload the extension

Click the refresh icon next to PixelCatch on `chrome://extensions`. You're done.

## Use

1. Make sure you're signed in to YouTube in Chrome (and a member / Premium subscriber if downloading gated content).
2. Click the toolbar icon to open the panel.
3. Paste the YouTube URL — including members-only video URLs.
4. Pick a quality preset.
5. Click **Download**.

The file lands in `~/Downloads`.

If you see "Join this channel to get access to members-only content" in the error message, your Chrome session isn't recognized as a member of that channel. Open the video in Chrome, confirm you can play it there, then retry.

## Permissions

| Permission | Why |
|---|---|
| `nativeMessaging` | Talk to the local `yt-dlp` helper |
| `storage` | Remember recent downloads in `chrome.storage.local` |
| `windows` | Open the popup as a free-floating panel |

There is intentionally no `tabs`, `cookies`, `identity`, or host permission. The extension never reads tab content and never talks to a remote server.

## Privacy & security

- **Local-only.** All downloads happen on your machine. PixelCatch makes no network requests outside of `yt-dlp`'s call to YouTube.
- **Cookie scoping.** The native host invokes `yt-dlp --cookies-from-browser chrome`. yt-dlp parses the on-disk cookie database to extract YouTube auth, but in HTTP requests only `youtube.com` cookies are sent — that's standard cookie scoping enforced by yt-dlp's HTTP layer. If you'd prefer not to grant disk access to the full cookie store, you can export a `youtube.com`-only `cookies.txt` from a browser extension and we can extend the host to accept that as input.
- **Defense in depth.** Both the popup and the native host independently validate the URL host, the quality preset, and the download path. The host's `FORMAT_PRESETS` map is the only set of `yt-dlp -f` strings ever passed to the subprocess — there's no path for arbitrary format DSL to reach `yt-dlp`.
- **Player client.** The host pins `--extractor-args youtube:player_client=web`. The `web` client is the only one that consistently respects auth cookies, which is what unlocks members-only and Premium content. Earlier configurations included `ios`, which silently ignored cookies and made the extension useless for its actual purpose.
- **Logs.** Written to `~/Library/Logs/com.pixelcatch.downloader.log` with mode `0600` (readable only by you).
- **No identity, no Gmail, no OAuth.** Earlier scaffolding for a Gmail integration has been removed entirely.

## Stable extension ID

Without a manifest `key`, Chrome assigns the unpacked extension a per-machine ID. Each time you reload on a new profile or machine, you must re-run `./native/install.sh <new-id>` so the native host's `allowed_origins` matches.

To make the ID stable across machines, upload the extension once as an unpublished item in the Chrome Web Store dashboard, copy the public key from the Package tab, and add it to `manifest.json` as `"key": "..."`.

## Development

```
.
├── manifest.json
├── background/service-worker.js   # opens the panel window
├── popup/                         # UI + native-messaging client
├── options/                       # privacy notice page
├── src/lib/storage.js             # tiny chrome.storage.local wrapper
├── native/
│   ├── downloader.py              # native messaging host
│   ├── install.sh                 # registers the host with Chrome
│   ├── runner.sh                  # generated by install.sh (gitignored)
│   └── manifest.template.json
└── tests/test_validation.py       # pytest suite for the host's validators
```

### Run the tests

```bash
pip install pytest
pytest -q
```

The suite covers the URL allow-list, the format enum (including 2K/4K and audio-only), the no-mp4-lock invariant for video presets, the download-path whitelist, and the progress parser. It does **not** invoke `yt-dlp` — that's covered by manual testing.

### Manual testing checklist

- [ ] Sign in to YouTube in Chrome with a Premium-active account; download a 1080p video and inspect the bitrate (should be Premium-tier).
- [ ] Sign in to a channel you're a member of; download a members-only video; confirm it succeeds.
- [ ] Sign out (or use a non-Premium account); download the same members-only URL; confirm a clean error message.
- [ ] Download a 4K-uploaded video at the **4K** preset; confirm the file is 2160p (use `mediainfo` or `ffprobe`).
- [ ] Download with **Audio only**; confirm the file is m4a/opus and there is no video stream.
- [ ] Try a non-YouTube URL — expect "URL host ... is not an allowed YouTube host".
- [ ] Confirm `~/Library/Logs/com.pixelcatch.downloader.log` exists with mode `600`.
- [ ] Confirm nothing is written to `/tmp/`.
- [ ] Close the popup mid-download — confirm the host exits cleanly.
