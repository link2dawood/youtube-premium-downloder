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

## The easy way: GitHub Actions does it for you

The repo ships `.github/workflows/release.yml`, which runs whenever you push a tag like `v1.0.0`. It:

1. Builds `PixelCatch-Helper.pkg` on a macOS runner.
2. Builds `PixelCatch-Helper-Setup.exe` on a Windows runner (with Inno Setup auto-installed).
3. Creates the GitHub Release and attaches all three files (`.pkg`, `.exe`, `install-linux.sh`).

You don't need a Mac, you don't need a Windows machine, you don't need Inno Setup. You just push a tag.

### Step 1 — push your code to GitHub

```bash
git push origin dev
```

### Step 2 — tag and push

```bash
git tag v1.0.0
git push origin v1.0.0
```

### Step 3 — watch the build run

Open https://github.com/link2dawood/youtube-premium-downloder/actions and you'll see "Release helper packages" running. Takes ~5 minutes (the macOS runner is the slowest because of the Inno Setup install on the Windows side runs in parallel).

### Step 4 — done

Your release appears at https://github.com/link2dawood/youtube-premium-downloder/releases/latest with the .pkg, .exe, and install-linux.sh attached. The popup download buttons start working immediately.

### Verify

Paste this in a browser tab:

```
https://github.com/link2dawood/youtube-premium-downloder/releases/latest/download/PixelCatch-Helper.pkg
```

If GitHub starts downloading the .pkg, you're done. Repeat for `PixelCatch-Helper-Setup.exe` and `install-linux.sh`.

### Triggering without a tag (e.g. for a draft preview build)

Open the Actions tab → "Release helper packages" → "Run workflow." Pick the version label and whether to mark the release as a draft. This is useful for testing the build pipeline without cutting a real public release.

---

## The manual way (only if you don't want to use Actions)

### Step 1 — push your code to GitHub

```bash
git push origin dev
```

### Step 2 — build the macOS .pkg

On your Mac:

```bash
./tools/build-pkg.sh --extension-id <your-extension-id>
```

Output: `dist/PixelCatch-Helper.pkg` and `dist/PixelCatch-Helper-1.0.0.pkg`.

### Step 3 — build the Windows .exe

This **must** run on a Windows machine. Inno Setup is Windows-only — there is no macOS or Linux version. On a Windows box with [Inno Setup 6](https://jrsoftware.org/isdl.php) installed:

```powershell
.\tools\build-win.ps1 -ExtensionId <your-extension-id>
```

If you don't have a Windows machine, **use the GitHub Actions workflow above** — that's exactly what it solves.

### Step 4 — Linux installer (already in the repo)

`tools/install-linux.sh` is uploaded as-is.

### Step 5 — create the GitHub release manually

1. Open https://github.com/link2dawood/youtube-premium-downloder/releases/new
2. Tag: `v1.0.0`. Target: `dev`.
3. Release title: `PixelCatch 1.0.0`.
4. Drag in: `PixelCatch-Helper.pkg`, `PixelCatch-Helper-Setup.exe`, `install-linux.sh`.
5. Set as latest. Publish.

---

## What end users will see when they download

Until you sign + notarize, every download triggers the OS's "this is unsigned" warning. Bake the bypass instructions into the release notes (the GitHub Actions workflow already does this) so users have a clear answer in front of them.

### macOS — "PixelCatch-Helper.pkg" Not Opened

Apple's Gatekeeper blocks unsigned .pkg files downloaded from the internet. The fix is per-user, takes 5 seconds:

1. **Right-click** the .pkg in Finder (don't double-click).
2. Choose **Open** from the menu.
3. Click **Open** in the dialog.
4. Enter admin password.

On macOS 13+ (Ventura/Sonoma/Sequoia) the right-click menu often doesn't show **Open** for unsigned installers. Workaround:

1. Double-click the .pkg → get blocked.
2. **System Settings → Privacy & Security** → scroll to bottom → **Open Anyway**.
3. Enter admin password.

### Windows — "Windows protected your PC"

SmartScreen blocks unsigned .exe files. The bypass:

1. Double-click the .exe → SmartScreen blocks it.
2. Click **More info** in the dialog.
3. Click **Run anyway**.
4. Click through the wizard.

### Linux

No equivalent friction. The installer is a shell script — there's nothing to "trust."

---

## Killing the warnings permanently (sign + notarize)

When you're ready to ship to non-technical users at scale, sign the installers so the warnings vanish:

| | macOS | Windows |
|---|---|---|
| Cost | $99/year (Apple Developer Program) | $200–$700/year (code-signing CA) |
| Tools | `productsign`, `xcrun notarytool`, `xcrun stapler` (already in `tools/README.md`) | `signtool` (in Windows SDK) |
| Effect | .pkg opens with no warning | EV cert: instant trust. OV cert: builds reputation after ~few hundred installs. |

Both can be wired into the GitHub Actions workflow as additional steps that run after the build but before the release. You'd add the cert as an Actions secret. Open a follow-up if you want me to add the signing steps once you have the certs in hand.

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
