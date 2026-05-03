# PixelCatch — Distribution & Install Flow

A short brief on how the extension and the Python helper actually reach end users after you publish.

## Two halves of the product

PixelCatch is two things glued together:

| Half | Lives in | Distributed via |
|---|---|---|
| The Chrome extension (`manifest.json`, popup, background worker, icons) | Chrome | Chrome Web Store |
| The native messaging host (`native/downloader.py`, `runner.sh`, the registered NM manifest in `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`) + `yt-dlp` itself | The user's local Mac | **Not** the Chrome Web Store — has to be installed separately |

This split is a hard constraint of the Chrome extension model. Web Store packages can't drop binaries on the user's filesystem, and Chrome runs all extensions in a sandbox that has no shell access. Everything heavy — yt-dlp, the cookie read, ffmpeg-style merging — has to live outside the sandbox in a native messaging host that the user installs themselves.

## What happens when a user installs from the Web Store

1. They click "Add to Chrome" on your store listing.
2. Chrome installs the extension package — JS, HTML, CSS, icons, manifest. That's it.
3. They click the toolbar icon, paste a URL, click Download.
4. The popup tries to `chrome.runtime.connectNative("com.pixelcatch.downloader")`.
5. Chrome looks in `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/` for `com.pixelcatch.downloader.json`.
6. **The file isn't there**, because nothing has installed it. Chrome returns "Specified native messaging host not found."
7. The user sees the friendly error PixelCatch now surfaces: *"PixelCatch helper isn't installed yet. Run native/install.sh ..."*

So the Python helper has to reach the user's machine somehow. There are three realistic options.

## Option A — GitHub + manual `install.sh` (where you are today)

Users:
1. Install the extension from the Web Store.
2. Click the extension; see the "helper not installed" message.
3. Go to your GitHub repo, clone it (or download a zip).
4. Open Terminal and run `./native/install.sh <extension-id>`.
5. Make sure `yt-dlp` is installed (`brew install yt-dlp`).
6. Reload the extension.

**Pros:** zero infrastructure to maintain. Already works today.
**Cons:** brutal UX for non-developers. Most non-technical users will give up at step 3.
**Web Store risk:** likely fine, as long as your listing description and screenshots make the manual install requirement obvious. Reviewers may still flag it.

## Option B — Notarized macOS `.pkg` installer (recommended for real launch)

Build a single `PixelCatch-Helper.pkg` that:

- Drops `downloader.py` into `~/Library/Application Support/PixelCatch/`
- Bundles a self-contained Python (or detects a system Python 3.8+)
- Installs `yt-dlp` in an isolated venv inside that directory (so you don't depend on Homebrew)
- Asks the user for their extension ID (or uses `~` of well-known production IDs once you have a stable manifest `key`) and writes the NM manifest to `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.pixelcatch.downloader.json`
- Sets up `~/Library/Logs/` with the right permissions

Tooling: build with `pkgbuild` + `productbuild`, then sign with your Apple Developer ID and submit to Apple notarization (required for unrestricted opening on modern macOS).

In the popup, when the helper isn't detected, link directly to the latest `.pkg` download URL. New onboarding becomes: install extension → first-run banner says "Download the helper" → user downloads and runs `.pkg` → done.

**Pros:** professional UX, works for non-developers, single download.
**Cons:** Apple Developer Program ($99/yr) for notarization, build pipeline to maintain, has to be re-signed for each release.

## Option C — Homebrew formula

Ship a tap (`brew install yourname/pixelcatch/pixelcatch-helper`) that installs the helper, registers the NM manifest, and depends on `yt-dlp`. Many YouTube-tooling power users already have Homebrew — this is the fastest path for them.

**Pros:** very low friction for technical users. Standard auto-update path.
**Cons:** non-technical users won't have Homebrew.

## Recommendation

For a real launch, do **both** B and C:

- A signed `.pkg` for the average user (linked from the popup and from your store listing's "Additional info" field)
- A Homebrew tap for power users

Until the `.pkg` exists, ship Option A and write the popup's "helper not installed" message to link directly to the GitHub README's install section. PixelCatch already does this with the friendly error mapping I added to `popup.js` — the message tells the user exactly what command to run.

## What the user does on each download (after the helper is installed)

Once the helper is installed, every download follows this sequence — no Python knowledge required from the user:

1. User clicks the toolbar icon, popup opens.
2. They paste a YouTube URL and pick a quality preset.
3. They click Download.
4. `popup.js` validates the URL and the format, then calls `chrome.runtime.connectNative("com.pixelcatch.downloader")`.
5. Chrome reads `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.pixelcatch.downloader.json`, sees that this extension's ID is in `allowed_origins`, and launches the script at `path` (your `runner.sh`, which `exec`s Python on `downloader.py`).
6. Chrome and the Python process talk over stdin/stdout using the native messaging frame format (4-byte length + JSON body).
7. `downloader.py` re-validates the message, runs `yt-dlp` with the user's Chrome cookies, and streams progress back to the popup.
8. The popup shows percent / speed / ETA in real time.
9. The file lands in `~/Downloads`. Done.

The user never touches Python directly after install. From their point of view it's a one-click download.

## Things to disclose on the Web Store listing

To avoid review rejection or confused users, make these explicit on your store page:

- "Requires a separate macOS helper to be installed (free, open source). Link in the description."
- "Requires you to be signed in to YouTube in Chrome."
- "macOS only at this time."
- A link to the helper download (.pkg or GitHub).
- A short screenshot showing the popup, the quality dropdown, and ideally the in-progress download bar.

## Updates

When you update the extension code, the Web Store rolls out the new version automatically.
When you update the Python helper, users need to download/install the new `.pkg` (or `brew upgrade`).
**Keep the native messaging *protocol* backward-compatible** — if you add new message fields, make them optional, and version the protocol if you ever have to break it. You will have a long tail of users running an old helper against a new extension.
