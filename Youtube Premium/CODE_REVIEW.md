# PixelCatch — Senior Architect / Engineering Code Review

Repo: `/Users/macbookpro/Documents/extensions/`
Branch: `dev` (HEAD `ff9eb5e` — "Remove Google OAuth/Gmail scaffold")
Reviewed: 2026-05-03

## Architecture at a glance

| Layer | Files | Role |
|---|---|---|
| Manifest | `manifest.json` | MV3, permissions: `nativeMessaging`, `storage`, `windows` |
| Background | `background/service-worker.js` | Opens popup as a panel window, stub message handler |
| UI | `popup/*`, `options/*` | Paste URL, pick quality, fire native download |
| Storage | `src/lib/storage.js` | Thin wrapper over `chrome.storage.local` |
| Native host | `native/downloader.py`, `install.sh`, `runner.sh` | yt-dlp runner, NM stdio protocol |
| Dead/orphaned | `src/gmail/*`, `src/config/env.js`, `client_secret_*.json` | Gmail/OAuth scaffold removed from runtime in HEAD but files still on disk |

The HEAD commit deleted the OAuth/Gmail flow from `manifest.json`, `background/service-worker.js`, and `popup/*`, but left the supporting modules, the OAuth client ID, the README content, and the privacy notice describing the removed feature. **The code base is in a half-deleted state.**

---

## Findings (severity-ordered)

### CRITICAL

**C1. Committed OAuth client ID + on-disk client secret JSON.**
`src/config/env.js:4` hard-codes `865980709872-…apps.googleusercontent.com`, and `client_secret_865980709872-….json` sits in the repo root (gitignored, but local). The JSON is the *"installed app"* OAuth client type — not the *Chrome Application* type Chrome extensions are supposed to use, so it was created incorrectly to begin with. Even though `client_secret_*.json` is gitignored, leaving it on disk in the working copy is a leak vector (screenshots, accidental `git add -f`, log uploads). Action: delete the JSON, and if OAuth is ever re-added, recreate the OAuth client as type "Chrome Application" (no secret).

**C2. `--cookies-from-browser chrome` siphons the entire Chrome cookie store.**
`native/downloader.py:68`. yt-dlp reads every Chrome cookie to authenticate. The manifest declares no `cookies` permission, and the README explicitly claims "The extension does not download YouTube videos directly." That claim is false: the local pipeline downloads videos using the user's authenticated Google session cookies. Combined with C3 (no validation in the host), any input that reaches the host will execute against the user's full cookie jar. Action: scope cookies to `--cookies-from-browser chrome:Default:youtube.com` (newer yt-dlp supports a domain filter) or have the user export a `cookies.txt` for member-only content rather than reading all Chrome cookies by default.

**C3. Native host trusts every message — no URL or format validation.**
`native/downloader.py:152-170`. URL is checked only for non-empty. `format` is taken verbatim from the message and appended to the yt-dlp argv. The popup constrains both today, but the native host has no way to know that. yt-dlp's format DSL is expressive (post-processors, output templates, write-info-json, etc.) and the host accepts any string. Defense in depth is mandatory here:

- Validate URL host against the same `YOUTUBE_HOSTS` set used in `popup.js`.
- Map a small symbolic enum (e.g. `"1080p"`) to vetted format strings on the Python side; reject unknown values.
- Do not accept arbitrary `downloadPath` (line 161); whitelist `~/Downloads` or `~/Movies`.

---

### HIGH

**H1. Race condition opening the panel window.**
`background/service-worker.js:12-27`. Two rapid clicks while `panelWindowId === null` both call `openPanel()` before either `windows.create` callback resolves, producing duplicate windows. Set a sentinel (e.g. `panelWindowId = "pending"`) immediately, and serialize behind a single in-flight promise.

**H2. README, `options/options.html`, and dead modules describe a feature that no longer ships.**
`README.md` documents Gmail message types, OAuth scopes, auth UI, and an `identity` permission that the manifest no longer requests. `options/options.html` is a privacy notice for the deleted scaffold (talks about `identity`, `gmail.readonly`, raw access tokens). `src/gmail/*` and `src/config/env.js` are orphaned imports of the removed flow. This is misleading to anyone auditing the extension (Chrome Web Store reviewers included) and ships unused code that contains the OAuth client ID. Either complete the removal or revert HEAD.

**H3. Native messaging host runs as single-shot, but docstring claims long-lived port.**
`native/downloader.py:2-4` says "long-lived connectNative port protocol (multiple messages)", but `main()` calls `read_message()` once and exits (line 145). Each download spawns a fresh process. Either implement a real read loop (`while True: msg = read_message(); if msg is None: break; ...`) or fix the docstring — current state will confuse future maintainers and breaks any client that assumes the contract.

**H4. Recent-link `<a target="_blank">` lacks explicit `noopener`.**
`popup/popup.js:140-142`. `rel="noreferrer"` implies `noopener` in modern Chrome, but explicit is safer and survives older browsers / future regressions.

---

### MEDIUM

**M1. Hard-coded developer path in shebang and runner.**
`native/downloader.py:1` is `#!/usr/local/bin/python3` (Intel-Mac homebrew). Unused in practice because `runner.sh` invokes Python explicitly, but breaks anyone who runs the script directly. Use `#!/usr/bin/env python3`.

**M2. Progress regex misses common yt-dlp output.**
`native/downloader.py:54-56`. The regex requires the literal `ETA <value>`. yt-dlp emits `(unknown)`, omits ETA on live streams and HLS fragments, and changes formatting between versions. Result: progress UI freezes at 0% for plenty of real downloads. Switch to `--progress-template` for stable, machine-readable output:

```python
"--progress-template", "[progress] %(progress.downloaded_bytes)s/%(progress.total_bytes)s %(progress.speed)s %(progress.eta)s"
```

**M3. `subprocess.Popen` is block-buffered.**
`native/downloader.py:83-89`. With `text=True` and the default `bufsize`, the `for line in proc.stdout` loop receives output in large blocks under some conditions. Pass `bufsize=1` (line-buffered) for snappier progress.

**M4. No timeout / disconnect handling on yt-dlp.**
A hung child process pins the host. Not catastrophic — Chrome SIGTERMs the host on port disconnect — but you should also wire `signal.signal(SIGTERM, …)` to kill `proc` cleanly.

**M5. Log file in `/tmp` leaks data on shared machines.**
`native/downloader.py:16`. On a multi-user box, `/tmp/ytdownloader_native.log` is readable by other users and exposes downloaded URLs and filenames. Move to `~/Library/Logs/com.extension.ytdownloader.log` with mode 0600.

**M6. The unused `chrome.runtime.onMessage` handler is dead code.**
`background/service-worker.js:44-62`. The popup never sends runtime messages — it talks to the native host directly via `connectNative`. Either keep the handler intentional and document it, or delete it.

**M7. `.claude/settings.json` contains an unfilled template token.**
The allowed Bash command references `__TRACKED_VAR__/Library/Application Support/YTDownloader/downloader.py`, which doesn't match the actual install location and looks like a placeholder that was never filled in.

**M8. No `key` field in `manifest.json`.**
README §1 instructs adding `key` to keep the unpacked extension ID stable. Without it, the native messaging `allowed_origins` (`chrome-extension://$EXTENSION_ID/`) breaks every time the extension is reloaded on a fresh profile. Document the consequences clearly or commit a `key` for development.

---

### LOW / Style

**L1. Quality select values embed yt-dlp DSL into the popup HTML.**
`popup/popup.html:39-43`. If yt-dlp's format DSL ever changes shape, every install breaks until users reload. Move format strings to the native host; have the popup send symbolic values (`"best" | "1080p" | "720p" | …`).

**L2. `storage.getMany` doesn't take a fallback.**
`src/lib/storage.js:15-18`. Inconsistent with `get`. Either document the asymmetry or unify.

**L3. No tests.** URL parsing, MIME building, and shell escaping are exactly the kinds of code that benefit from a small unit-test suite. There is none.

**L4. `runner.sh` is missing `set -e`.** Minor robustness gap.

**L5. `popup.js:221-222` swallows `saveRecentYouTubeLink` failures via a dangling `.then()`** with no `.catch()`. A storage write error would silently disappear.

**L6. `options/options.html` `<title>` and `<h1>` say "Extension Foundation"** — a generic placeholder, not "Pixel Catch."

**L7. `encodeBase64` in `mime.js`** uses byte-by-byte `String.fromCharCode` concatenation; O(n²) on long strings. Moot if Gmail module stays deleted.

---

## Recommended fixes, in commit-sized chunks

1. **Finish the Gmail removal.** Delete `src/gmail/`, `src/config/env.js`, `client_secret_*.json`. Strip Gmail/OAuth/identity content from `README.md` and `options/options.html`. Replace the privacy notice with one that reflects the *actual* current permissions — including the cookie read by yt-dlp.

2. **Harden the native host.** URL host whitelist, format enum, optional download-path whitelist, switch to `--progress-template`, set `bufsize=1`, move log to `~/Library/Logs/`, fix the shebang, add SIGTERM handler.

3. **Fix the panel race.** Promise-gate `openPanel`, drop the unused message handler.

4. **Doc cleanup.** Rewrite README around the actual flow (popup → connectNative → yt-dlp), document the `key` requirement and the `install.sh` path-pinning behavior, fix the options-page title and copy.

5. **Tests.** A handful of unit tests for `validateAndNormalizeYouTubeUrl` (popup), the URL/format validators (native host), and the NM frame protocol would catch regressions cheaply.

---

[View this report](computer:///Users/macbookpro/Documents/extensions/Youtube%20Premium/CODE_REVIEW.md)
