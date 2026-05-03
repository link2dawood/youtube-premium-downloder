<#
.SYNOPSIS
    Install the PixelCatch Helper on Windows.

.DESCRIPTION
    Drops the helper into %LOCALAPPDATA%\PixelCatch, downloads yt-dlp.exe if
    needed, and registers the Chrome native messaging host in the Windows
    registry so Chrome / Edge / Brave / Vivaldi can launch it.

    Requires Python 3 in PATH. If you don't have Python, install it from
    python.org (check "Add Python to PATH" during install) and re-run this
    script.

.PARAMETER ExtensionId
    One or more Chrome extension IDs (comma-separated). Find yours at
    chrome://extensions with Developer mode enabled.

.PARAMETER SkipYtDlpDownload
    Skip downloading yt-dlp.exe (use a copy you've put in the install dir
    yourself, e.g. for offline installs).

.EXAMPLE
    .\install-windows.ps1 -ExtensionId aaaabbbbccccddddaaaabbbbccccdddd

.EXAMPLE
    .\install-windows.ps1 -ExtensionId aaaa...,bbbb...

.NOTES
    Per-user install — does NOT need Administrator. Registers under
    HKEY_CURRENT_USER. To install for all users on the machine, run an
    elevated build via Inno Setup (tools/win/installer.iss).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ExtensionId,

    [switch]$SkipYtDlpDownload
)

$ErrorActionPreference = "Stop"

$HostName = "com.pixelcatch.downloader"
$InstallDir = Join-Path $env:LOCALAPPDATA "PixelCatch"
$ManifestPath = Join-Path $InstallDir "$HostName.json"
$RunnerPath = Join-Path $InstallDir "runner.bat"
$DownloaderPath = Join-Path $InstallDir "downloader.py"
$YtDlpPath = Join-Path $InstallDir "yt-dlp.exe"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Source = Join-Path $RepoRoot "native\downloader.py"

# Browsers we register with. Each entry is [label, registry-root].
$Browsers = @(
    @{ Label = "Google Chrome"; Root = "HKCU:\Software\Google\Chrome\NativeMessagingHosts" },
    @{ Label = "Chromium";       Root = "HKCU:\Software\Chromium\NativeMessagingHosts" },
    @{ Label = "Microsoft Edge"; Root = "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts" },
    @{ Label = "Brave";          Root = "HKCU:\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts" },
    @{ Label = "Vivaldi";        Root = "HKCU:\Software\Vivaldi\NativeMessagingHosts" }
)

# ---------- Verify Python 3 ----------

$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        $version = & $found.Source -c "import sys; print(sys.version_info[0])" 2>$null
        if ($version -eq "3") {
            $python = $found.Source
            break
        }
    }
}

if (-not $python) {
    Write-Host "Python 3 not found in PATH." -ForegroundColor Red
    Write-Host "Install Python 3 from https://www.python.org/downloads/windows/"
    Write-Host "and CHECK 'Add Python to PATH' during installation, then re-run this script."
    exit 1
}

Write-Host "Python 3: $python"

# ---------- Stage helper ----------

if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
}

Copy-Item $Source $DownloaderPath -Force
Write-Host "Installed: $DownloaderPath"

# ---------- Get yt-dlp.exe ----------

if (-not $SkipYtDlpDownload) {
    if (Test-Path $YtDlpPath) {
        Write-Host "yt-dlp.exe already present — leaving it alone."
    } else {
        $url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
        Write-Host "Downloading yt-dlp.exe..."
        Invoke-WebRequest -Uri $url -OutFile $YtDlpPath -UseBasicParsing
        Write-Host "Downloaded: $YtDlpPath"
    }
}

# ---------- Generate runner.bat ----------

# Chrome launches the host with a stripped PATH; this batch wrapper sets the
# install dir on PATH (so the bundled yt-dlp.exe wins) and execs Python.
$runnerContent = @"
@echo off
setlocal
set "PATH=$InstallDir;%PATH%"
"$python" "$DownloaderPath"
"@
Set-Content -Path $RunnerPath -Value $runnerContent -Encoding ASCII
Write-Host "Generated: $RunnerPath"

# ---------- Build the JSON manifest ----------

$ids = $ExtensionId -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ }
foreach ($id in $ids) {
    if ($id -notmatch "^[a-p]{32}$") {
        Write-Warning "'$id' doesn't look like a valid Chrome extension ID."
    }
}
$origins = $ids | ForEach-Object { "chrome-extension://$_/" }

$manifest = [ordered]@{
    name = $HostName
    description = "PixelCatch YouTube downloader native host"
    path = $RunnerPath
    type = "stdio"
    allowed_origins = $origins
}

$manifestJson = $manifest | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText($ManifestPath, $manifestJson)
Write-Host "Manifest:  $ManifestPath"

# ---------- Register with every detected browser ----------

$registered = 0
foreach ($browser in $Browsers) {
    # Only register if the browser's parent key exists, i.e. the browser is
    # actually installed and has been launched at least once.
    $parent = Split-Path $browser.Root -Parent
    if (-not (Test-Path $parent)) { continue }

    if (-not (Test-Path $browser.Root)) {
        New-Item -Path $browser.Root -Force | Out-Null
    }
    $hostKey = Join-Path $browser.Root $HostName
    if (-not (Test-Path $hostKey)) {
        New-Item -Path $hostKey -Force | Out-Null
    }
    Set-ItemProperty -Path $hostKey -Name "(default)" -Value $ManifestPath
    Write-Host "Registered with: $($browser.Label)"
    $registered++

    # Sweep the legacy host name from earlier dev builds.
    $legacy = Join-Path $browser.Root "com.extension.ytdownloader"
    if (Test-Path $legacy) { Remove-Item $legacy -Recurse -Force }
}

if ($registered -eq 0) {
    Write-Warning "No Chromium-family browsers detected for this user."
    Write-Warning "Open Chrome (or Edge / Brave / Chromium) once, then re-run this script."
    exit 1
}

Write-Host ""
Write-Host "PixelCatch Helper installed." -ForegroundColor Green
Write-Host "  Helper:    $InstallDir"
Write-Host "  Logs:      $InstallDir\downloader.log"
Write-Host "  Browsers:  $registered registered"
Write-Host ""
Write-Host "Reload PixelCatch in your browser's chrome://extensions and you're set."
