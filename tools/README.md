# PixelCatch Helper — build & install pipelines

Everything in this directory is for *you* (the developer). End users run a single installer for their OS — `.pkg` on macOS, `.exe` on Windows, `install-linux.sh` on Linux.

## At a glance

| Platform | Build script | Output | Install script for power users |
|---|---|---|---|
| macOS    | `tools/build-pkg.sh`    | `dist/PixelCatch-Helper-<v>.pkg`        | n/a (the .pkg is already double-clickable) |
| Windows  | `tools/build-win.ps1`   | `dist/PixelCatch-Helper-Setup-<v>.exe`  | `tools/install-windows.ps1` |
| Linux    | (no build step)         | n/a — install script is the deliverable | `tools/install-linux.sh` |

End users on **macOS** double-click the `.pkg`. End users on **Windows** double-click the `.exe`. End users on **Linux** clone the repo and run `tools/install-linux.sh --extension-id <id>` from a terminal — Linux users are technical enough that this is fine, and the script auto-registers with every Chromium-family browser they have installed.

---

## macOS — build the `.pkg`

```bash
./tools/build-pkg.sh --extension-id <your-extension-id>
```

Output: `dist/PixelCatch-Helper-<version>.pkg`. See the [macOS section](#macos--detailed) below for sign + notarize.

---

## Windows — build the `.exe`

Run on a Windows machine with **Inno Setup 6** installed (free, https://jrsoftware.org/isdl.php).

```powershell
.\tools\build-win.ps1 -ExtensionId <your-extension-id>
```

Output: `dist\PixelCatch-Helper-Setup-<version>.exe`. The installer:

- Bundles a portable Python 3.12 (~25 MB) so users **don't need to install Python** themselves.
- Bundles `yt-dlp.exe` (Windows standalone build).
- Drops everything into `%LOCALAPPDATA%\PixelCatch`.
- Writes registry entries for Chrome, Edge, Brave, Chromium, and Vivaldi (`HKCU\Software\<browser>\NativeMessagingHosts\com.pixelcatch.downloader`).
- No Administrator privileges required.

To distribute outside your own machine without Microsoft SmartScreen flagging it, code-sign with `signtool` and an EV (or OV with reputation) certificate — see the in-line note at the end of `build-win.ps1`.

---

## Linux — there's no build, just an install script

Linux users clone the repo (or you ship a tarball), then run:

```bash
./tools/install-linux.sh --extension-id <your-extension-id>
```

The script:

- Detects which Chromium-family browser(s) the user has (Chrome, Chromium, Brave, Edge, Vivaldi) and registers with each one.
- Downloads the right `yt-dlp` binary for the architecture (`yt-dlp_linux`, `yt-dlp_linux_aarch64`, etc.).
- Installs everything per-user into `~/.local/share/PixelCatch/` — no `sudo` needed.
- Logs go to `~/.local/state/pixelcatch/downloader.log` (XDG state spec).
- Requires Python 3 in PATH (every modern distro has it; the script tells the user how to install if missing).

If you eventually want `.deb` / `.rpm` packages, wrap this script in `dpkg-deb` / `rpmbuild` later — the install logic stays the same.

---

## Uninstallers

Each platform ships its own:

```
sudo ./tools/uninstall-helper.sh                # macOS
.\tools\uninstall-windows.ps1                   # Windows
./tools/uninstall-linux.sh                      # Linux

# Add --logs (Mac/Linux) or -RemoveLogs (Windows) to also wipe log files.
```

The Windows installer also adds an **Add or remove programs** entry, so users can uninstall the normal Windows way too.

---

## macOS — detailed

### Prerequisites
- macOS with Xcode CLT (`xcode-select --install`) — provides `pkgbuild`, `productbuild`.
- Internet on first build to download `yt-dlp_macos`; cached after.
- Apple Developer Program ($99/yr) for sign + notarize.

### Sign + notarize
```bash
productsign --sign 'Developer ID Installer: Your Name (TEAMID)' \
    dist/PixelCatch-Helper-1.0.0.pkg \
    dist/PixelCatch-Helper-1.0.0-signed.pkg

xcrun notarytool submit dist/PixelCatch-Helper-1.0.0-signed.pkg \
    --apple-id you@example.com --team-id TEAMID \
    --password app-specific-password --wait

xcrun stapler staple dist/PixelCatch-Helper-1.0.0-signed.pkg
```

Distribute the `-signed.pkg` from your website / GitHub releases / S3.

### What the postinstall does
Runs as root after the user enters their admin password. Generates `runner.sh`, writes `/Library/Google/Chrome/NativeMessagingHosts/com.pixelcatch.downloader.json` (system-wide so every macOS user account on the machine sees it), creates `/Library/Logs/PixelCatch/`, and removes legacy manifest names. See `tools/pkg/scripts/postinstall.template`.

---

## Windows — detailed

### Prerequisites
- Windows 10 / 11 with PowerShell 5.1+ (built in).
- Inno Setup 6 (https://jrsoftware.org/isdl.php — free).
- Internet on first build (downloads Python embeddable + `yt-dlp.exe`; cached).
- Optional: code-signing certificate ($200–$700/yr depending on type).

### Sign with `signtool`
```powershell
signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a `
    "dist\PixelCatch-Helper-Setup-1.0.0.exe"
```

Without a signed cert, Windows SmartScreen will show "Windows protected your PC — Don't run" the first few times users download it. The user can click "More info → Run anyway" but it scares non-technical people. EV certs bypass SmartScreen instantly; OV certs need to build reputation over a few hundred installs.

### What the installer does
Inno Setup unpacks the payload to `%LOCALAPPDATA%\PixelCatch`, writes registry keys for every supported Chromium-family browser, then a `[Code]` section regenerates `runner.bat` with absolute paths so Chrome (which strips PATH when launching native hosts) can find Python. Uninstall is wired through Inno's standard `[UninstallDelete]` so the helper is fully removed via Add or remove programs.

---

## Linux — detailed

### Prerequisites
- Any Linux distro with Python 3 in PATH.
- `curl` or `wget` for the yt-dlp download (most distros ship at least one).

### `--skip-ytdlp-download`
If you're packaging the install script for an environment without internet (corporate network, air-gapped machine), use the `--skip-ytdlp-download` flag and pre-place a `yt-dlp` binary at `~/.local/share/PixelCatch/yt-dlp` before running.

### Auto-detect of multiple browsers
The script registers with every Chromium-family browser whose config dir already exists. Open Chrome / Brave / Edge once before running the installer, and they'll all be picked up.

---

## File layout

```
tools/
├── README.md                      ← this file
│
├── build-pkg.sh                   ← macOS .pkg builder
├── pkg/
│   ├── Distribution.xml
│   ├── Resources/
│   │   ├── welcome.html
│   │   └── conclusion.html
│   └── scripts/postinstall.template
│
├── install-linux.sh               ← Linux per-user installer (end-user runnable)
├── uninstall-linux.sh             ← Linux uninstaller
│
├── build-win.ps1                  ← Windows .exe builder (run on Windows)
├── install-windows.ps1            ← Windows per-user installer (PowerShell)
├── uninstall-windows.ps1          ← Windows uninstaller (PowerShell)
└── win/
    └── installer.iss.template     ← Inno Setup config (template)
```
