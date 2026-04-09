import { storage } from "../src/lib/storage.js";

const STORAGE_KEYS = {
  recentYouTubeLinks: "recentYouTubeLinks"
};

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
  youtubeUrlInput: document.querySelector("#youtube-url-input")
};

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
    link.rel = "noreferrer";
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
  try {
    normalizedEntry = validateAndNormalizeYouTubeUrl(elements.youtubeUrlInput.value);
  } catch (error) {
    updateProgress({ label: "Invalid URL", statusMsg: error.message, isError: true });
    return;
  }

  elements.downloadButton.disabled = true;
  updateProgress({ label: "Connecting…", statusMsg: "Starting download…" });

  let port;
  try {
    port = chrome.runtime.connectNative("com.extension.ytdownloader");
  } catch (error) {
    updateProgress({ label: "Error", statusMsg: `Could not connect: ${error.message}`, isError: true });
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
      updateProgress({
        percent: 100,
        label: msg.filename ?? "Complete",
        statusMsg: msg.message ?? "Download complete. Check ~/Downloads.",
        isDone: true
      });
      saveRecentYouTubeLink({ url: normalizedEntry.url, openedAt: new Date().toISOString() })
        .then(refreshRecentLinks);
      elements.downloadButton.disabled = false;
      port.disconnect();
    } else if (msg.type === "error") {
      updateProgress({ label: "Failed", statusMsg: msg.error ?? "Download failed.", isError: true });
      elements.downloadButton.disabled = false;
      port.disconnect();
    }
  });

  port.onDisconnect.addListener(() => {
    if (chrome.runtime.lastError) {
      updateProgress({ label: "Error", statusMsg: chrome.runtime.lastError.message, isError: true });
    }
    elements.downloadButton.disabled = false;
  });

  port.postMessage({
    action: "download",
    url: normalizedEntry.url,
    format: elements.qualitySelect.value
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

// --- Init ---

refreshRecentLinks().catch(console.error);
