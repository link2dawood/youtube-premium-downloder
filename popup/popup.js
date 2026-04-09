import {
  getGoogleOAuthClientId,
  getGoogleOAuthScopes,
  hasGoogleOAuthClientId
} from "../src/config/env.js";
import { storage } from "../src/lib/storage.js";

const STORAGE_KEYS = {
  popupMeta: "popupMeta",
  recentYouTubeLinks: "recentYouTubeLinks",
  savedNote: "savedNote"
};

const MAX_RECENT_YOUTUBE_LINKS = 5;
const YOUTUBE_STUDIO_URL = "https://studio.youtube.com/";
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
  authEmail: document.querySelector("#auth-email"),
  authStatus: document.querySelector("#auth-status"),
  connectButton: document.querySelector("#connect-button"),
  downloadButton: document.querySelector("#download-button"),
  qualitySelect: document.querySelector("#quality-select"),
  downloadPanel: document.querySelector("#download-panel"),
  downloadFilename: document.querySelector("#download-filename"),
  downloadPercent: document.querySelector("#download-percent"),
  downloadSpeed: document.querySelector("#download-speed"),
  downloadEta: document.querySelector("#download-eta"),
  downloadStatus: document.querySelector("#download-status"),
  progressFill: document.querySelector("#progress-fill"),
  feedbackMessage: document.querySelector("#feedback-message"),
  grantedScopes: document.querySelector("#granted-scopes"),
  lastOpened: document.querySelector("#last-opened"),
  lastAuth: document.querySelector("#last-auth"),
  lastVideo: document.querySelector("#last-video"),
  noteForm: document.querySelector("#note-form"),
  noteInput: document.querySelector("#note-input"),
  openLastVideoButton: document.querySelector("#open-last-video-button"),
  openPrivacyNoticeButton: document.querySelector("#open-privacy-notice-button"),
  openYouTubeStudioButton: document.querySelector("#open-youtube-studio-button"),
  oauthStatus: document.querySelector("#oauth-status"),
  popupCount: document.querySelector("#popup-count"),
  refreshButton: document.querySelector("#refresh-button"),
  reconnectButton: document.querySelector("#reconnect-button"),
  recentLinksList: document.querySelector("#recent-links-list"),
  resetButton: document.querySelector("#reset-button"),
  scopeStatus: document.querySelector("#scope-status"),
  signOutButton: document.querySelector("#sign-out-button"),
  tokenStatus: document.querySelector("#token-status"),
  tokenStatusMessage: document.querySelector("#token-status-message"),
  tokenUpdated: document.querySelector("#token-updated"),
  workerStatus: document.querySelector("#worker-status"),
  youtubeForm: document.querySelector("#youtube-form"),
  youtubeUrlInput: document.querySelector("#youtube-url-input")
};

let isAuthActionPending = false;
let lastAuthState = {
  email: "",
  grantedScopes: [],
  lastAuthenticatedAt: null,
  status: "signed_out"
};

function formatDate(value) {
  if (!value) {
    return "Never";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

function setFeedback(message, isError = false) {
  elements.feedbackMessage.textContent = message;
  elements.feedbackMessage.style.color = isError ? "#b91c1c" : "#0f766e";
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

function formatScopes(scopes) {
  if (!Array.isArray(scopes) || scopes.length === 0) {
    return "Not granted yet";
  }

  return scopes.join(", ");
}

function formatRecentLinkLabel(entry) {
  if (!entry?.url) {
    return "Never";
  }

  try {
    const url = new URL(entry.url);
    return url.pathname === "/watch"
      ? url.searchParams.get("v") || entry.url
      : `${url.hostname}${url.pathname}`;
  } catch (error) {
    return entry.url;
  }
}

function extractYouTubeVideoId(url) {
  const hostname = url.hostname.replace(/^www\./, "");

  if (hostname === "youtu.be") {
    return url.pathname.split("/").filter(Boolean)[0] || "";
  }

  if (hostname === "youtube.com" || hostname === "m.youtube.com" || hostname === "music.youtube.com") {
    if (url.pathname === "/watch") {
      return url.searchParams.get("v") || "";
    }

    const [, route, value] = url.pathname.split("/");
    if (route === "shorts" || route === "embed" || route === "live") {
      return value || "";
    }
  }

  if (hostname === "youtube-nocookie.com") {
    const [, route, value] = url.pathname.split("/");
    if (route === "embed") {
      return value || "";
    }
  }

  return "";
}

function validateAndNormalizeYouTubeUrl(input) {
  const rawValue = String(input || "").trim();

  if (!rawValue) {
    throw new Error("Paste a YouTube URL first.");
  }

  let parsedUrl;

  try {
    parsedUrl = new URL(rawValue);
  } catch (error) {
    throw new Error("Enter a valid URL that starts with http:// or https://.");
  }

  if (!["http:", "https:"].includes(parsedUrl.protocol)) {
    throw new Error("Only http:// and https:// YouTube URLs are supported.");
  }

  if (!YOUTUBE_HOSTS.has(parsedUrl.hostname)) {
    throw new Error("That is not a supported YouTube URL.");
  }

  const videoId = extractYouTubeVideoId(parsedUrl);

  if (!videoId) {
    throw new Error("Could not find a YouTube video ID in that URL.");
  }

  const normalizedUrl = new URL("https://www.youtube.com/watch");
  normalizedUrl.searchParams.set("v", videoId);

  return {
    originalUrl: parsedUrl.toString(),
    videoId,
    url: normalizedUrl.toString()
  };
}

function renderRecentLinks(recentLinks = []) {
  elements.recentLinksList.replaceChildren();

  if (!recentLinks.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "No videos opened yet.";
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
    link.textContent = entry.url;
    openedAt.textContent = `Opened ${formatDate(entry.openedAt)} (${formatRecentLinkLabel(entry)})`;
    item.append(link, " ", openedAt);
    elements.recentLinksList.append(item);
  }

  elements.openLastVideoButton.disabled = false;
  elements.lastVideo.textContent = formatRecentLinkLabel(recentLinks[0]);
}

function formatTokenStatus(authState) {
  switch (authState.tokenStatus) {
    case "ready":
      return "Ready";
    case "refreshed":
      return "Refreshed";
    case "refreshing":
      return "Refreshing…";
    case "refresh_failed":
      return "Refresh Failed";
    case "authorization_required":
      return "Reconnect Needed";
    case "error":
      return "Error";
    case "signed_out":
      return "Signed Out";
    default:
      return "Checking…";
  }
}

function setAuthButtonsDisabled(isDisabled) {
  isAuthActionPending = isDisabled;

  const buttons = [
    elements.connectButton,
    elements.reconnectButton,
    elements.signOutButton,
    elements.refreshButton,
    elements.resetButton
  ];

  for (const button of buttons) {
    button.disabled = isDisabled;
    button.classList.toggle("button-disabled", isDisabled);
  }
}

async function sendRuntimeMessage(type) {
  const response = await chrome.runtime.sendMessage({ type });

  if (!response?.ok) {
    throw new Error(response?.error || "Unknown runtime error");
  }

  return response;
}

function renderAuthState(authState = {}) {
  lastAuthState = {
    ...lastAuthState,
    ...authState
  };

  const isConnected = lastAuthState.status === "connected";
  const disableAllButtons = isAuthActionPending;
  const disableSignOut = disableAllButtons || !isConnected;
  elements.authStatus.textContent = isConnected ? "Connected" : "Signed out";
  elements.authEmail.textContent = lastAuthState.email || "Not connected";
  elements.lastAuth.textContent = formatDate(lastAuthState.lastAuthenticatedAt);
  elements.tokenStatus.textContent = formatTokenStatus(lastAuthState);
  elements.tokenUpdated.textContent = formatDate(lastAuthState.lastTokenStatusAt);
  elements.grantedScopes.textContent = formatScopes(lastAuthState.grantedScopes);
  elements.tokenStatusMessage.textContent = lastAuthState.tokenStatusMessage || "Waiting for Google auth state.";
  elements.connectButton.disabled = disableAllButtons;
  elements.reconnectButton.disabled = disableAllButtons;
  elements.refreshButton.disabled = disableAllButtons;
  elements.resetButton.disabled = disableAllButtons;
  elements.signOutButton.disabled = disableSignOut;
}

async function refreshView() {
  const [popupMeta, recentYouTubeLinks, savedNote] = await Promise.all([
    storage.get(STORAGE_KEYS.popupMeta, { count: 0, lastOpenedAt: null }),
    storage.get(STORAGE_KEYS.recentYouTubeLinks, []),
    storage.get(STORAGE_KEYS.savedNote, "")
  ]);

  elements.popupCount.textContent = String(popupMeta.count ?? 0);
  elements.lastOpened.textContent = formatDate(popupMeta.lastOpenedAt);
  elements.noteInput.value = savedNote;
  renderRecentLinks(recentYouTubeLinks);
  elements.oauthStatus.textContent = hasGoogleOAuthClientId()
    ? "Configured"
    : "Missing in src/config/env.js";
  elements.scopeStatus.textContent = getGoogleOAuthScopes().join(", ");
  elements.workerStatus.textContent = "Loading…";
  elements.tokenStatus.textContent = "Checking…";
  elements.tokenUpdated.textContent = "Loading…";
  elements.tokenStatusMessage.textContent = "Refreshing extension auth status.";

  try {
    const response = await sendRuntimeMessage("app:get-status");
    elements.workerStatus.textContent = response.serviceWorkerActive ? "Ready" : "Unavailable";
    renderAuthState(response.authState);
  } catch (error) {
    elements.workerStatus.textContent = "Unavailable";
    renderAuthState();
    setFeedback("Background worker did not respond.", true);
  }
}

async function updatePopupMeta() {
  const popupMeta = await storage.get(STORAGE_KEYS.popupMeta, {
    count: 0,
    lastOpenedAt: null
  });

  const nextMeta = {
    count: (popupMeta.count ?? 0) + 1,
    lastOpenedAt: new Date().toISOString()
  };

  await storage.set(STORAGE_KEYS.popupMeta, nextMeta);
}

async function handleNoteSave(event) {
  event.preventDefault();
  await storage.set(STORAGE_KEYS.savedNote, elements.noteInput.value.trim());
  setFeedback("Saved note to local storage.");
  await refreshView();
}

async function handleReset() {
  await storage.remove([
    STORAGE_KEYS.popupMeta,
    STORAGE_KEYS.recentYouTubeLinks,
    STORAGE_KEYS.savedNote
  ]);
  elements.youtubeUrlInput.value = "";
  setFeedback("Cleared popup demo data.");
  await refreshView();
}

async function saveRecentYouTubeLink(entry) {
  const recentLinks = await storage.get(STORAGE_KEYS.recentYouTubeLinks, []);
  const dedupedLinks = recentLinks.filter((item) => item.url !== entry.url);
  const nextRecentLinks = [entry, ...dedupedLinks].slice(0, MAX_RECENT_YOUTUBE_LINKS);
  await storage.set(STORAGE_KEYS.recentYouTubeLinks, nextRecentLinks);
}

async function openYouTubeLink(entry) {
  await chrome.tabs.create({ url: entry.url });
  await saveRecentYouTubeLink(entry);
  await refreshView();
}

async function openYouTubeStudio() {
  await chrome.tabs.create({ url: YOUTUBE_STUDIO_URL });
  setFeedback("Opened YouTube Studio in a new tab.");
}

async function openPrivacyNotice() {
  await chrome.runtime.openOptionsPage();
}

async function handleYouTubeOpen(event) {
  event.preventDefault();
  const normalizedEntry = validateAndNormalizeYouTubeUrl(elements.youtubeUrlInput.value);

  await openYouTubeLink({
    ...normalizedEntry,
    openedAt: new Date().toISOString()
  });

  elements.youtubeUrlInput.value = normalizedEntry.url;
  setFeedback("Opened YouTube video in a new tab.");
}

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

async function handleOpenLastVideo() {
  const recentLinks = await storage.get(STORAGE_KEYS.recentYouTubeLinks, []);
  const [lastLink] = recentLinks;

  if (!lastLink?.url) {
    throw new Error("There is no saved YouTube video yet.");
  }

  await openYouTubeLink({
    ...lastLink,
    openedAt: new Date().toISOString()
  });
  setFeedback("Opened the last YouTube video again.");
}

async function initializePopup() {
  await updatePopupMeta();
  await refreshView();

  if (hasGoogleOAuthClientId()) {
    setFeedback(`OAuth client ID loaded: ${getGoogleOAuthClientId()}`);
  } else {
    setFeedback("Add your Google OAuth client ID in src/config/env.js.");
  }
}

async function runAuthAction(type, successMessage) {
  setAuthButtonsDisabled(true);
  renderAuthState(lastAuthState);

  try {
    const response = await sendRuntimeMessage(type);
    renderAuthState(response.authState);
    setFeedback(successMessage);
    await refreshView();
  } catch (error) {
    console.error(`Failed auth action: ${type}`, error);
    setFeedback(error.message || "Google authentication failed.", true);
  } finally {
    setAuthButtonsDisabled(false);
    renderAuthState(lastAuthState);
  }
}

elements.noteForm.addEventListener("submit", (event) => {
  handleNoteSave(event).catch((error) => {
    console.error("Failed to save note", error);
    setFeedback("Could not save note.", true);
  });
});

elements.youtubeForm.addEventListener("submit", (event) => {
  handleYouTubeOpen(event).catch((error) => {
    console.error("Failed to open YouTube URL", error);
    setFeedback(error.message || "Could not open the YouTube URL.", true);
  });
});

elements.refreshButton.addEventListener("click", () => {
  refreshView().catch((error) => {
    console.error("Failed to refresh popup", error);
    setFeedback("Could not refresh popup.", true);
  });
});

elements.resetButton.addEventListener("click", () => {
  handleReset().catch((error) => {
    console.error("Failed to reset popup data", error);
    setFeedback("Could not reset popup data.", true);
  });
});

elements.openLastVideoButton.addEventListener("click", () => {
  handleOpenLastVideo().catch((error) => {
    console.error("Failed to open last video", error);
    setFeedback(error.message || "Could not open the last YouTube video.", true);
  });
});

elements.downloadButton.addEventListener("click", () => {
  handleYouTubeDownload();
});

elements.openYouTubeStudioButton.addEventListener("click", () => {
  openYouTubeStudio().catch((error) => {
    console.error("Failed to open YouTube Studio", error);
    setFeedback("Could not open YouTube Studio.", true);
  });
});

elements.openPrivacyNoticeButton.addEventListener("click", () => {
  openPrivacyNotice().catch((error) => {
    console.error("Failed to open privacy notice", error);
    setFeedback("Could not open the privacy notice.", true);
  });
});

elements.connectButton.addEventListener("click", () => {
  runAuthAction("auth:connect", "Connected to Google.").catch((error) => {
    console.error("Connect action failed", error);
  });
});

elements.reconnectButton.addEventListener("click", () => {
  runAuthAction("auth:reconnect", "Reconnected to Google.").catch((error) => {
    console.error("Reconnect action failed", error);
  });
});

elements.signOutButton.addEventListener("click", () => {
  runAuthAction("auth:sign-out", "Signed out and cleared cached tokens.").catch((error) => {
    console.error("Sign-out action failed", error);
  });
});

elements.closeButton.addEventListener("click", () => {
  window.close();
});

initializePopup().catch((error) => {
  console.error("Failed to initialize popup", error);
  setFeedback("Popup failed to initialize.", true);
});
