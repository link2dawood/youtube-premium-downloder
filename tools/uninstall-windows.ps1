<#
.SYNOPSIS
    Uninstall the PixelCatch Helper on Windows.

.PARAMETER RemoveLogs
    Also delete the helper log file at %LOCALAPPDATA%\PixelCatch\downloader.log.
#>
[CmdletBinding()]
param(
    [switch]$RemoveLogs
)

$ErrorActionPreference = "SilentlyContinue"

$HostName = "com.pixelcatch.downloader"
$InstallDir = Join-Path $env:LOCALAPPDATA "PixelCatch"

$RegistryRoots = @(
    "HKCU:\Software\Google\Chrome\NativeMessagingHosts",
    "HKCU:\Software\Chromium\NativeMessagingHosts",
    "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts",
    "HKCU:\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    "HKCU:\Software\Vivaldi\NativeMessagingHosts"
)

$removed = 0

foreach ($root in $RegistryRoots) {
    $key = Join-Path $root $HostName
    if (Test-Path $key) {
        Remove-Item $key -Recurse -Force
        Write-Host "Unregistered: $key"
        $removed++
    }
    # Also wipe the legacy host name.
    $legacy = Join-Path $root "com.extension.ytdownloader"
    if (Test-Path $legacy) { Remove-Item $legacy -Recurse -Force }
}

if (Test-Path $InstallDir) {
    if (-not $RemoveLogs) {
        # Preserve the log unless --RemoveLogs.
        $log = Join-Path $InstallDir "downloader.log"
        $logBackup = $null
        if (Test-Path $log) {
            $logBackup = Join-Path $env:TEMP "pixelcatch-downloader.log"
            Move-Item $log $logBackup -Force
        }
        Remove-Item $InstallDir -Recurse -Force
        if ($logBackup) {
            New-Item -ItemType Directory -Path $InstallDir | Out-Null
            Move-Item $logBackup (Join-Path $InstallDir "downloader.log") -Force
        }
    } else {
        Remove-Item $InstallDir -Recurse -Force
    }
    Write-Host "Removed: $InstallDir"
    $removed++
}

if ($removed -eq 0) {
    Write-Host "PixelCatch Helper does not appear to be installed."
} else {
    Write-Host ""
    Write-Host "PixelCatch Helper uninstalled." -ForegroundColor Green
}
