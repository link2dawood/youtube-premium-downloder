import { storage } from "../src/lib/storage.js";

const STORAGE_KEYS = {
  recentYouTubeLinks: "recentYouTubeLinks",
  signInConfirmed: "signInConfirmedAt"
};

const NATIVE_HOST = "com.pixelcatch.downloader";

// --- Helper download endpoints ---
//
// Points at the published helper installers for each OS. The release assets
// must be uploaded to the "latest" GitHub release of the repo below for the
// buttons to actually deliver something:
//   - PixelCatch-Helper.pkg          (macOS)
//   - PixelCatch-Helper-Setup.exe    (Windows)
//   - install-linux.sh               (Linux)
const HELPER_REPO = "link2dawood/youtube-premium-downloder";
const HELPER_DOWNLOAD_BASE = `https://github.com/${HELPER_REPO}/releases/latest/download`;
const HELPER_DOWNLOADS = {
  mac:     `${HELPER_DOWNLOAD_BASE}/PixelCatch-Helper.pkg`,
  windows: `${HELPER_DOWNLOAD_BASE}/PixelCatch-Helper-Setup.exe`,
  linux:   `${HELPER_DOWNLOAD_BASE}/install-linux.sh`,
};
const HELPER_HELP_URL = `https://github.com/${HELPER_REPO}/tree/dev#install`;

// How long to wait for the helper to reply to a ping before assuming it's
// not installed. The native host launches a fresh process per port, so a
// generous timeout covers cold-start latency on slow Macs / spinning disks.
const PING_TIMEOUT_MS = 3000;

const MAX_RECENT_YOUTUBE_LINKS = 5;

const YOUTUBE_HOSTS = new Set([
  "youtu.be",
  "m.youtube.com",
  "music.youtube.com",
  "www.youtube-nocookie.com",
  "www.youtube.com",
  "youtube-nocookie.com",
  "youtube.com"
]);

const ALLOWED_FORMATS = new Set([
  "best",
  "4k",
  "2k",
  "1080p",
  "720p",
  "480p",
  "audio"
]);

const elements = {
  closeButton: document.querySelector("#close-button"),
  downloadButton: document.querySelector("#download-button"),
  qualitySelect: document.querySelector("#quality-select"),
  downloadPanel: document.querySelector("#download-panel"),
  downloadFilename: document.querySelector("#download-filename"),
  downloadPercent: document.querySelector("#download-percent"),
  downloadSpeed: document.querySelector("#download-speed"),
  downloadEta: document.querySelector("#download-eta"),
  downloadStatus: document.querySelector("#download-status"),
  progressFill: document.querySelector("#progress-fill"),
  openLastVideoButton: document.querySelector("#open-last-video-button"),
  openYouTubeStudioButton: document.querySelector("#open-youtube-studio-button"),
  recentLinksList: document.querySelector("#recent-links-list"),
  lastVideo: document.querySelector("#last-video"),
  youtubeForm: document.querySelector("#youtube-form"),
  youtubeUrlInput: document.querySelector("#youtube-url-input"),
  signinBanner: document.querySelector("#signin-banner"),
  openYouTubeButton: document.querySelector("#open-youtube-button"),
  confirmSigninButton: document.querySelector("#confirm-signin-button"),
  setupPanel: document.querySelector("#setup-panel"),
  setupDownloadButton: document.querySelector("#setup-download-button"),
  setupRecheckButton: document.querySelector("#setup-recheck-button"),
  setupHelpButton: document.querySelector("#setup-help-button"),
  setupPlatformNote: document.querySelector("#setup-platform-note"),
  setupStatus: document.querySelector("#setup-status"),
  mainPanel: document.querySelector("#main-panel")
};

// --- Helper presence detection ---
//
// We try to ping the native host on popup open. The host responds with
// `{type: "pong"}` and exits. If the host isn't installed, Chrome fires
// onDisconnect with a "not found" lastError. Either way we resolve quickly
// and decide whether to show the setup panel or the regular UI.

function pingNativeHost(timeoutMs = PING_TIMEOUT_MS) {
  return new Promise((resolve) => {
    let resolved = false;
    let port;
    const finish = (result) => {
      if (resolved) return;
      resolved = true;
      try { port?.disconnect(); } catch { /* already disconnected */ }
      resolve(result);
    };

    try {
      port = chrome.runtime.connectNative(NATIVE_HOST);
    } catch (error) {
      finish({ ok: false, error: error?.message || String(error) });
      return;
    }

    const timer = setTimeout(() => {
      finish({ ok: false, error: "timeout" });
    }, timeoutMs);

    port.onMessage.addListener((msg) => {
      if (msg && msg.type === "pong") {
        clearTimeout(timer);
        finish({ ok: true, version: msg.version, platform: msg.platform });
      }
    });

    port.onDisconnect.addListener(() => {
      clearTimeout(timer);
      const lastError = chrome.runtime.lastError?.message || "disconnected";
      finish({ ok: false, error: lastError });
    });

    try {
      port.postMessage({ action: "ping" });
    } catch (error) {
      clearTimeout(timer);
      finish({ ok: false, error: error?.message || String(error) });
    }
  });
}

// --- Setup panel ---

function setSetupStatus(text, kind) {
  elements.setupStatus.textContent = text || "";
  elements.setupStatus.className = "setup-status" + (kind ? ` ${kind}` : "");
}

function showSetupPanel(reason) {
  const plat = detectPlatform();
  const labels = {
    mac:     "Download for macOS (.pkg)",
    windows: "Download for Windows (.exe)",
    linux:   "Download installer for Linux",
    unknown: "Download installer",
  };
  const notes = {
    mac:     "Double-click the .pkg, enter your admin password, done.",
    windows: "Double-click the .exe and click through the wizard.",
    linux:   "Run the script with your extension ID — see the help link below.",
    unknown: "See the help link below for your operating system.",
  };
  elements.setupDownloadButton.textContent = labels[plat] || labels.unknown;
  elements.setupPlatformNote.textContent = notes[plat] || notes.unknown;

  // Hide the regular download UI while setup is required.
  elements.setupPanel.classList.remove("hidden");
  elements.mainPanel.classList.add("hidden");
  if (reason === "ping-failed") {
    setSetupStatus("Helper not detected on this computer.", "");
  } else {
    setSetupStatus("");
  }
}

function showMainUI() {
  elements.setupPanel.classList.add("hidden");
  elements.mainPanel.classList.remove("hidden");
}

async function checkHelperPresence({ silent = false } = {}) {
  if (!silent) setSetupStatus("Checking…");
  const result = await pingNativeHost();
  if (result.ok) {
    showMainUI();
    return result;
  }
  showSetupPanel("ping-failed");
  return result;
}

function downloadHelperForPlatform() {
  const plat = detectPlatform();
  const url = HELPER_DOWNLOADS[plat] || HELPER_HELP_URL;
  if (!HELPER_REPO || HELPER_REPO.includes("REPO_OWNER")) {
    setSetupStatus(
      "Download URL hasn't been configured yet — open popup.js and set HELPER_REPO to your GitHub <owner>/<repo> before shipping.",
      "error"
    );
    return;
  }
  chrome.tabs.create({ url });
  setSetupStatus("Opened the download in a new tab. Run the installer, then click “check again”.", "");
}

async function recheckHelper() {
  setSetupStatus("Checking…");
  const result = await pingNativeHost();
  if (result.ok) {
    setSetupStatus("Helper detected — version " + (result.version || "?") + ". You're all set.", "success");
    setTimeout(showMainUI, 600);
  } else {
    setSetupStatus(
      "Still not detected. If you just installed it, give it a few seconds, then click again. " +
      "Make sure you ran the installer matching your operating system.",
      "error"
    );
  }
}

// --- Sign-in gate ---
//
// We can't actually verify the user is signed in (the extension has no
// `cookies` permission, and that's intentional). The banner is a one-time
// onboarding nudge: it shows on first run, the user clicks "I'm signed in"
// once, and we remember that confirmation in chrome.storage.local. They can
// always reopen the link to YouTube via this banner.

async function refreshSignInBanner() {
  const confirmedAt = await storage.get(STORAGE_KEYS.signInConfirmed, null);
  if (confirmedAt) {
    elements.signinBanner.classList.add("hidden");
  } else {
    elements.signinBanner.classList.remove("hidden");
  }
}

async function handleConfirmSignIn() {
  await storage.set(STORAGE_KEYS.signInConfirmed, new Date().toISOString());
  await refreshSignInBanner();
}

// --- Helpers ---

function formatDate(value) {
  if (!value) return "Never";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

function updateProgress({ percent = 0, speed = "", eta = "", label = "Downloading…", statusMsg = "", isError = false, isDone = false } = {}) {
  elements.downloadPanel.classList.remove("hidden");
  elements.downloadFilename.textContent = label;
  elements.downloadPercent.textContent = `${Math.round(percent)}%`;
  elements.progressFill.style.width = `${percent}%`;
  elements.progressFill.classList.toggle("complete", isDone);
  elements.downloadSpeed.textContent = speed;
  elements.downloadEta.textContent = eta ? `ETA ${eta}` : "";
  elements.downloadStatus.textContent = statusMsg;
  elements.downloadStatus.className = `download-status-msg${isError ? " error" : ""}`;
}

function formatRecentLinkLabel(entry) {
  if (!entry?.url) return "Never";
  try {
    const url = new URL(entry.url);
    return url.pathname === "/watch"
      ? url.searchParams.get("v") || entry.url
      : `${url.hostname}${url.pathname}`;
  } catch {
    return entry.url;
  }
}

function extractYouTubeVideoId(url) {
  const hostname = url.hostname.replace(/^www\./, "");

  if (hostname === "youtu.be") {
    return url.pathname.split("/").filter(Boolean)[0] || "";
  }

  if (["youtube.com", "m.youtube.com", "music.youtube.com"].includes(hostname)) {
    if (url.pathname === "/watch") return url.searchParams.get("v") || "";
    const [, route, value] = url.pathname.split("/");
    if (["shorts", "embed", "live"].includes(route)) return value || "";
  }

  if (hostname === "youtube-nocookie.com") {
    const [, route, value] = url.pathname.split("/");
    if (route === "embed") return value || "";
  }

  return "";
}

function validateAndNormalizeYouTubeUrl(input) {
  const rawValue = String(input || "").trim();
  if (!rawValue) throw new Error("Paste a YouTube URL first.");

  let parsedUrl;
  try {
    parsedUrl = new URL(rawValue);
  } catch {
    throw new Error("Enter a valid URL that starts with http:// or https://.");
  }

  if (!["http:", "https:"].includes(parsedUrl.protocol)) {
    throw new Error("Only http:// and https:// YouTube URLs are supported.");
  }

  if (!YOUTUBE_HOSTS.has(parsedUrl.hostname)) {
    throw new Error("That is not a supported YouTube URL.");
  }

  const videoId = extractYouTubeVideoId(parsedUrl);
  if (!videoId) throw new Error("Could not find a YouTube video ID in that URL.");

  const normalizedUrl = new URL("https://www.youtube.com/watch");
  normalizedUrl.searchParams.set("v", videoId);

  return { originalUrl: parsedUrl.toString(), videoId, url: normalizedUrl.toString() };
}

function detectPlatform() {
  const ua = (navigator.userAgentData?.platform || navigator.platform || "").toLowerCase();
  if (ua.includes("mac")) return "mac";
  if (ua.includes("win")) return "windows";
  if (ua.includes("linux")) return "linux";
  return "unknown";
}

function installInstructionFor(plat) {
  switch (plat) {
    case "mac":
      return "Install the PixelCatch Helper .pkg (double-click PixelCatch-Helper-1.0.0.pkg).";
    case "windows":
      return "Install the PixelCatch Helper (double-click PixelCatch-Helper-Setup.exe).";
    case "linux":
      return "Run tools/install-linux.sh --extension-id <your-extension-id> from a terminal.";
    default:
      return "Install the PixelCatch Helper for your operating system — see the README.";
  }
}

function logPathFor(plat) {
  switch (plat) {
    case "mac":     return "~/Library/Logs/com.pixelcatch.downloader.log";
    case "windows": return "%LOCALAPPDATA%\\PixelCatch\\downloader.log";
    case "linux":   return "~/.local/state/pixelcatch/downloader.log";
    default:        return "the helper log file";
  }
}

function friendlyNativeError(rawMessage) {
  const msg = String(rawMessage || "");
  const plat = detectPlatform();
  if (/specified native messaging host not found/i.test(msg)) {
    return `PixelCatch helper isn't installed yet. ${installInstructionFor(plat)}`;
  }
  if (/native host has exited/i.test(msg)) {
    return `The PixelCatch helper crashed unexpectedly. Check ${logPathFor(plat)} for details.`;
  }
  if (/access to the specified native messaging host is forbidden/i.test(msg)) {
    return "The helper rejected this extension's ID. Reinstall the helper with the current extension ID from chrome://extensions.";
  }
  return msg || "Unknown native messaging error.";
}

function validateFormat(value) {
  const format = String(value || "").trim().toLowerCase();
  if (!ALLOWED_FORMATS.has(format)) {
    throw new Error(`Unsupported quality "${value}".`);
  }
  return format;
}

// --- Recent links ---

function renderRecentLinks(recentLinks = []) {
  elements.recentLinksList.replaceChildren();

  if (!recentLinks.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "No videos downloaded yet.";
    elements.recentLinksList.append(item);
    elements.openLastVideoButton.disabled = true;
    elements.lastVideo.textContent = "Never";
    return;
  }

  for (const entry of recentLinks) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    const openedAt = document.createElement("span");
    link.className = "recent-link";
    link.href = entry.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = formatRecentLinkLabel(entry);
    openedAt.textContent = ` — ${formatDate(entry.openedAt)}`;
    item.append(link, openedAt);
    elements.recentLinksList.append(item);
  }

  elements.openLastVideoButton.disabled = false;
  elements.lastVideo.textContent = formatRecentLinkLabel(recentLinks[0]);
}

async function saveRecentYouTubeLink(entry) {
  const recentLinks = await storage.get(STORAGE_KEYS.recentYouTubeLinks, []);
  const deduped = recentLinks.filter((item) => item.url !== entry.url);
  await storage.set(STORAGE_KEYS.recentYouTubeLinks, [entry, ...deduped].slice(0, MAX_RECENT_YOUTUBE_LINKS));
}

async function refreshRecentLinks() {
  const recentLinks = await storage.get(STORAGE_KEYS.recentYouTubeLinks, []);
  renderRecentLinks(recentLinks);
}

// --- YouTube open ---

async function handleYouTubeOpen(event) {
  event.preventDefault();
  const entry = validateAndNormalizeYouTubeUrl(elements.youtubeUrlInput.value);
  await chrome.tabs.create({ url: entry.url });
}

async function handleOpenLastVideo() {
  const recentLinks = await storage.get(STORAGE_KEYS.recentYouTubeLinks, []);
  const [lastLink] = recentLinks;
  if (!lastLink?.url) throw new Error("No saved video yet.");
  await chrome.tabs.create({ url: lastLink.url });
}

// --- Download ---

function handleYouTubeDownload() {
  let normalizedEntry;
  let format;
  try {
    normalizedEntry = validateAndNormalizeYouTubeUrl(elements.youtubeUrlInput.value);
    format = validateFormat(elements.qualitySelect.value);
  } catch (error) {
    updateProgress({ label: "Invalid input", statusMsg: error.message, isError: true });
    return;
  }

  elements.downloadButton.disabled = true;
  updateProgress({ label: "Connecting…", statusMsg: "Starting download…" });

  let port;
  let finished = false;

  try {
    port = chrome.runtime.connectNative(NATIVE_HOST);
  } catch (error) {
    updateProgress({
      label: "Error",
      statusMsg: friendlyNativeError(error.message),
      isError: true,
    });
    elements.downloadButton.disabled = false;
    return;
  }

  port.onMessage.addListener((msg) => {
    if (msg.type === "progress") {
      updateProgress({
        percent: msg.percent ?? 0,
        speed: msg.speed ?? "",
        eta: msg.eta ?? "",
        label: msg.filename ?? "Downloading…",
        statusMsg: msg.status ?? ""
      });
    } else if (msg.type === "done") {
      finished = true;
      updateProgress({
        percent: 100,
        label: msg.filename ?? "Complete",
        statusMsg: msg.message ?? "Download complete. Check ~/Downloads.",
        isDone: true
      });
      saveRecentYouTubeLink({ url: normalizedEntry.url, openedAt: new Date().toISOString() })
        .then(refreshRecentLinks)
        .catch((err) => console.error("Failed to save recent link", err));
      elements.downloadButton.disabled = false;
    } else if (msg.type === "error") {
      finished = true;
      updateProgress({ label: "Failed", statusMsg: msg.error ?? "Download failed.", isError: true });
      elements.downloadButton.disabled = false;
    }
  });

  port.onDisconnect.addListener(() => {
    // Chrome always fires onDisconnect with lastError="Native host has exited"
    // even on a clean successful exit — only show it if we never got a done/error message.
    if (!finished && chrome.runtime.lastError) {
      updateProgress({
        label: "Error",
        statusMsg: friendlyNativeError(chrome.runtime.lastError.message),
        isError: true,
      });
    }
    elements.downloadButton.disabled = false;
  });

  port.postMessage({
    action: "download",
    url: normalizedEntry.url,
    format
  });
}

// --- Event listeners ---

elements.closeButton.addEventListener("click", () => window.close());

elements.youtubeForm.addEventListener("submit", (event) => {
  handleYouTubeOpen(event).catch((error) => {
    console.error("Failed to open YouTube URL", error);
  });
});

elements.downloadButton.addEventListener("click", () => handleYouTubeDownload());

elements.openLastVideoButton.addEventListener("click", () => {
  handleOpenLastVideo().catch((error) => {
    console.error("Failed to open last video", error);
  });
});

elements.openYouTubeStudioButton.addEventListener("click", () => {
  chrome.tabs.create({ url: "https://studio.youtube.com/" });
});

elements.openYouTubeButton.addEventListener("click", () => {
  chrome.tabs.create({ url: "https://www.youtube.com/" });
});

elements.confirmSigninButton.addEventListener("click", () => {
  handleConfirmSignIn().catch((error) => {
    console.error("Failed to record sign-in confirmation", error);
  });
});

elements.setupDownloadButton.addEventListener("click", () => {
  downloadHelperForPlatform();
});

elements.setupRecheckButton.addEventListener("click", () => {
  recheckHelper().catch((error) => {
    console.error("Recheck failed", error);
    setSetupStatus("Couldn't run the check. " + (error?.message || ""), "error");
  });
});

elements.setupHelpButton.addEventListener("click", () => {
  chrome.tabs.create({ url: HELPER_HELP_URL });
});

// --- Init ---

refreshRecentLinks().catch(console.error);
refreshSignInBanner().catch(console.error);

// Helper presence check runs on every popup open. While it's pending we
// keep both panels hidden to avoid a flash of the wrong UI; whichever
// resolves wins. Quietly swap to the right view when we know.
elements.setupPanel.classList.add("hidden");
elements.mainPanel.classList.add("hidden");
checkHelperPresence({ silent: true }).catch((error) => {
  console.error("Helper presence check failed", error);
  showSetupPanel("ping-failed");
});
