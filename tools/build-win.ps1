<#
.SYNOPSIS
    Build the PixelCatch Helper one-click Windows installer (.exe).

.DESCRIPTION
    Assembles the payload (downloader.py, yt-dlp.exe, Windows embeddable
    Python) and runs Inno Setup to compile a single PixelCatch-Helper-Setup-
    <version>.exe that non-technical users can double-click.

.PARAMETER ExtensionId
    One or more Chrome extension IDs (comma-separated) the installer should
    register the native messaging host with. After publishing to the Web Store
    you'll bake your stable production ID in here.

.PARAMETER Version
    Helper version. Defaults to the version field in manifest.json.

.PARAMETER InnoSetupCompiler
    Path to ISCC.exe. Defaults to the standard Inno Setup 6 install location.

.EXAMPLE
    .\tools\build-win.ps1 -ExtensionId aaaabbbbccccddddaaaabbbbccccdddd

.NOTES
    Run on Windows. Requires:
      - Inno Setup 6 installed (https://jrsoftware.org/isdl.php — free)
      - Internet on first build (downloads yt-dlp.exe and Python embed; cached after)

    Output: dist\PixelCatch-Helper-Setup-<version>.exe

    To distribute outside your own machine without SmartScreen scaring users,
    code-sign the .exe with an EV / OV code signing certificate. See README.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ExtensionId,

    [string]$Version = "",

    [string]$InnoSetupCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ToolsDir = Join-Path $RepoRoot "tools"
$WinDir = Join-Path $ToolsDir "win"
$BuildDir = Join-Path $RepoRoot "build\win"
$CacheDir = Join-Path $RepoRoot "build\cache"
$DistDir = Join-Path $RepoRoot "dist"
$PayloadDir = Join-Path $BuildDir "payload"

# Resolve version from manifest.json if not supplied.
if ([string]::IsNullOrWhiteSpace($Version)) {
    $manifest = Get-Content (Join-Path $RepoRoot "manifest.json") | ConvertFrom-Json
    $Version = $manifest.version
}

# Validate Inno Setup is available.
if (-not (Test-Path $InnoSetupCompiler)) {
    Write-Error "Inno Setup compiler not found at $InnoSetupCompiler. Install Inno Setup 6 from https://jrsoftware.org/isdl.php or pass -InnoSetupCompiler <path>."
}

# Embeddable Python — change PYTHON_VERSION to bump.
$PythonVersion = "3.12.7"
$PythonZip = "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonZip"
$YtDlpUrl = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"

Write-Host "PixelCatch Helper Windows build"
Write-Host "  version:       $Version"
Write-Host "  extension IDs: $ExtensionId"
Write-Host ""

# ---------- Clean + create dirs ----------
if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $PayloadDir, $CacheDir, $DistDir | Out-Null

# ---------- yt-dlp.exe ----------
$YtDlpCache = Join-Path $CacheDir "yt-dlp.exe"
if (-not (Test-Path $YtDlpCache)) {
    Write-Host "Downloading yt-dlp.exe..."
    Invoke-WebRequest -Uri $YtDlpUrl -OutFile $YtDlpCache -UseBasicParsing
}
Copy-Item $YtDlpCache (Join-Path $PayloadDir "yt-dlp.exe")

# ---------- Embeddable Python ----------
$PythonCache = Join-Path $CacheDir $PythonZip
if (-not (Test-Path $PythonCache)) {
    Write-Host "Downloading $PythonZip..."
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonCache -UseBasicParsing
}
$EmbedDir = Join-Path $PayloadDir "python-embed"
New-Item -ItemType Directory -Force -Path $EmbedDir | Out-Null
Expand-Archive -Path $PythonCache -DestinationPath $EmbedDir -Force

# Embeddable Python ships with a `pythonNN._pth` file that disables `import
# site`. The downloader.py only uses stdlib modules so this is fine, but we
# make sure the embed dir itself is on sys.path so any future stdlib hooks
# work.
Get-ChildItem -Path $EmbedDir -Filter "python*._pth" | ForEach-Object {
    $content = Get-Content $_.FullName
    if (-not ($content -match "^\.")) { Add-Content -Path $_.FullName -Value "." }
}

# ---------- downloader.py ----------
Copy-Item (Join-Path $RepoRoot "native\downloader.py") (Join-Path $PayloadDir "downloader.py")

# ---------- Generate manifest.json ----------
$ids = $ExtensionId -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ }
foreach ($id in $ids) {
    if ($id -notmatch "^[a-p]{32}$") {
        Write-Warning "'$id' doesn't look like a valid Chrome extension ID."
    }
}
$origins = $ids | ForEach-Object { "chrome-extension://$_/" }
$manifest = [ordered]@{
    name = "com.pixelcatch.downloader"
    description = "PixelCatch YouTube downloader native host (v$Version)"
    path = ""  # filled in at runtime by the .iss CurStepChanged hook? No —
              # the registry points directly at this manifest, and the
              # manifest's `path` is the runner.bat that the installer drops
              # into {app}. ISCC's [Code] section can't easily edit JSON, so
              # we hardcode the typical install path here. If the user picks
              # a custom dir during install, the registry still points to the
              # right manifest, but the manifest's `path` would be stale.
              # Mitigation: use the standard {localappdata}\PixelCatch path.
    type = "stdio"
    allowed_origins = $origins
}
# Bake in the standard install path; matches DefaultDirName in the .iss.
$installPath = Join-Path $env:LOCALAPPDATA "PixelCatch"
$manifest.path = (Join-Path $installPath "runner.bat")
$manifestJson = $manifest | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText((Join-Path $PayloadDir "manifest.json"), $manifestJson)

# Also stage a placeholder runner.bat so Inno Setup's [Files] doesn't
# complain. The .iss [Code] section regenerates it post-install with the
# user's real install path.
Set-Content -Path (Join-Path $PayloadDir "runner.bat") -Value "@echo off`r`nrem placeholder; rewritten by installer post-install`r`n" -Encoding ASCII

# ---------- Substitute the .iss template ----------
$issTemplate = Get-Content (Join-Path $WinDir "installer.iss.template") -Raw
$iss = $issTemplate.Replace("__VERSION__", $Version)
# We don't need to substitute __EXTENSION_IDS_JSON_LIST__ in the .iss because
# the IDs live inside the staged manifest.json instead.
$issPath = Join-Path $WinDir "installer.generated.iss"
[System.IO.File]::WriteAllText($issPath, $iss)

# ---------- Compile with Inno Setup ----------
Write-Host "Compiling installer with Inno Setup..."
& $InnoSetupCompiler $issPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup compilation failed (exit $LASTEXITCODE)."
}

$out = Join-Path $DistDir "PixelCatch-Helper-Setup-$Version.exe"
Write-Host ""
Write-Host "Built: $out" -ForegroundColor Green
Write-Host ""
Write-Host "To distribute without SmartScreen warnings, code-sign with signtool:"
Write-Host "  signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a `"$out`""
