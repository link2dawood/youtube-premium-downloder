# PixelCatch — how to make the download buttons work

The popup buttons point at GitHub release URLs that **don't exist yet**. You have to publish a release on `link2dawood/youtube-premium-downloder` once. After that, every future install of the extension just works — users click the button, the .pkg / .exe downloads, they install it.

---

## What the popup is hitting (read-only — don't change these)

| OS | URL the popup opens |
|---|---|
| macOS | `https://github.com/link2dawood/youtube-premium-downloder/releases/latest/download/PixelCatch-Helper.pkg` |
| Windows | `https://github.com/link2dawood/youtube-premium-downloder/releases/latest/download/PixelCatch-Helper-Setup.exe` |
| Linux | `https://github.com/link2dawood/youtube-premium-downloder/releases/latest/download/install-linux.sh` |

These resolve to whatever asset is named exactly that on whatever release you've marked **latest** on GitHub. To make them resolve, you upload three files with those exact names to a GitHub release.

---

## One-time setup: cut your first release

### Step 1 — push your code to GitHub

```bash
git push origin dev
```

(I haven't pushed for you. Your `dev` branch is one commit ahead of `origin/dev`.)

### Step 2 — build the macOS .pkg

On your Mac:

```bash
./tools/build-pkg.sh --extension-id <your-extension-id>
```

You get two files in `dist/`:

```
dist/PixelCatch-Helper-1.0.0.pkg     ← versioned (keep for archives)
dist/PixelCatch-Helper.pkg           ← unversioned (this is what the popup downloads)
```

Upload **both** to the release in step 5.

### Step 3 — build the Windows .exe

On a Windows machine with [Inno Setup 6](https://jrsoftware.org/isdl.php) installed:

```powershell
.\tools\build-win.ps1 -ExtensionId <your-extension-id>
```

You get:

```
dist\PixelCatch-Helper-Setup-1.0.0.exe     ← versioned
dist\PixelCatch-Helper-Setup.exe           ← unversioned (what the popup downloads)
```

If you don't have a Windows machine, you can skip this for the first release — the macOS button will work, and Windows users will just see a "release exists but doesn't have this file" 404 until you cut a release with the .exe added. Better to do both before launching.

### Step 4 — Linux installer (already in the repo, no build needed)

The Linux installer is `tools/install-linux.sh`. Just upload that file as-is to the release.

### Step 5 — create the GitHub release

1. Open https://github.com/link2dawood/youtube-premium-downloder/releases/new in your browser.
2. **Choose a tag**: type `v1.0.0` and click "Create new tag: v1.0.0 on publish."
3. **Target**: pick `dev` (since you don't want to merge to main yet — GitHub releases can be cut from any branch).
4. **Release title**: "PixelCatch 1.0.0"
5. **Description**: paste the short release notes (template below).
6. **Attach files**: drag these into the "Attach binaries" area:
   - `dist/PixelCatch-Helper.pkg` (unversioned, REQUIRED — popup hits this)
   - `dist/PixelCatch-Helper-1.0.0.pkg` (versioned, optional but nice)
   - `dist/PixelCatch-Helper-Setup.exe` (unversioned, REQUIRED for Windows users)
   - `dist/PixelCatch-Helper-Setup-1.0.0.exe` (versioned, optional)
   - `tools/install-linux.sh` (REQUIRED for Linux users)
7. Make sure **"Set as the latest release"** is checked.
8. Click **Publish release**.

The popup buttons start working the moment you click Publish.

### Step 6 — verify

In a fresh browser tab, paste:

```
https://github.com/link2dawood/youtube-premium-downloder/releases/latest/download/PixelCatch-Helper.pkg
```

If GitHub starts downloading the .pkg, the popup is now wired up correctly. Repeat for the .exe and install-linux.sh URLs.

---

## Release notes template

Paste this into the GitHub release description and edit:

```markdown
## PixelCatch 1.0.0 — first release

Download YouTube Premium and members-only videos at any resolution up to 4K (2160p), or audio-only.

### Install

- **macOS:** download `PixelCatch-Helper.pkg`, double-click, enter your admin password.
- **Windows:** download `PixelCatch-Helper-Setup.exe`, double-click, click through the wizard.
- **Linux:** download `install-linux.sh`, then run it from a terminal:
  ```bash
  bash install-linux.sh --extension-id <your-extension-id>
  ```

After installing the helper, install the Chrome extension and click its icon. The popup will detect the helper automatically.

### Requirements

- A signed-in YouTube account in Chrome.
- Channel membership for members-only videos; YouTube Premium for the enhanced 1080p stream.
```

---

## Subsequent releases (1.0.1, 1.1, 1.2, etc.)

1. Edit `manifest.json` — bump the `version` field to e.g. `"1.0.1"`.
2. Edit `native/downloader.py` — bump `HELPER_VERSION = "1.0.1"`.
3. Commit + push to `dev`.
4. Re-run `tools/build-pkg.sh` and `tools/build-win.ps1`.
5. Cut a new GitHub release tagged `v1.0.1`. Upload the same set of files.
6. Make sure the new release is marked **latest**. The popup buttons now serve the new files automatically.

---

## What if you cut a release with only one OS's installer?

That's fine — the missing OS's button will return 404 in the user's browser. Not great UX, but doesn't break the popup. The popup's "check again" button still works for users who installed the helper some other way.

---

## Optional polish before launch (not required to ship)

These improve UX but cost time/money:

- **Sign + notarize the macOS .pkg** ($99/year Apple Developer Program). Without this, macOS Gatekeeper warns the user the .pkg "can't be opened because Apple cannot check it for malicious software." They can right-click → Open to bypass, but it scares non-technical users. See `tools/README.md` for the `productsign` + `notarytool` commands.

- **Code-sign the Windows .exe** ($200–$700/year for OV cert; EV cert is more). Without this, Windows SmartScreen says "Windows protected your PC — Don't run." The user can click "More info → Run anyway." See `tools/README.md` for the `signtool` command.

- **Custom domain for downloads** (e.g. `https://pixelcatch.app/download/mac`). Buy a domain, host a tiny redirect page, point the popup at the friendly URL. GitHub release URLs work fine, they just look hacky in the address bar.
