// Popup — thin client of the background service worker.
//
// All native messaging and download state lives in the worker. We just:
//   1. Send "start-download" / "cancel-download" / "ping-host" messages.
//   2. Subscribe to "job-update" broadcasts and re-render the affected card.
//   3. On open, fetch the current job list from the worker so reopening the
//      side panel shows whatever's in flight or recently finished.
//
// Closing the side panel does NOT cancel downloads anymore — the worker
// owns the ports and keeps running.

import { storage } from "../src/lib/storage.js";

const STORAGE_KEYS = {
  recentYouTubeLinks: "recentYouTubeLinks",
  signInConfirmed: "signInConfirmedAt",
};

const HELPER_REPO = "link2dawood/youtube-premium-downloder";
const HELPER_DOWNLOAD_BASE = `https://github.com/${HELPER_REPO}/releases/latest/download`;
const HELPER_DOWNLOADS = {
  mac:     `${HELPER_DOWNLOAD_BASE}/PixelCatch-Helper.pkg`,
  windows: `${HELPER_DOWNLOAD_BASE}/PixelCatch-Helper-Setup.exe`,
  linux:   `${HELPER_DOWNLOAD_BASE}/install-linux.sh`,
};
const HELPER_HELP_URL = `https://github.com/${HELPER_REPO}/tree/dev#install`;

const MAX_RECENT_YOUTUBE_LINKS = 5;

const YOUTUBE_HOSTS = new Set([
  "youtu.be",
  "m.youtube.com",
  "music.youtube.com",
  "www.youtube-nocookie.com",
  "www.youtube.com",
  "youtube-nocookie.com",
  "youtube.com",
]);

const ALLOWED_FORMATS = new Set(["best", "4k", "2k", "1080p", "720p", "480p", "audio"]);
const FORMAT_LABELS = {
  best: "Best available",
  "4k": "4K (2160p)",
  "2k": "2K (1440p)",
  "1080p": "1080p",
  "720p": "720p",
  "480p": "480p",
  audio: "Audio only",
};

const els = {
  closeButton: document.querySelector("#close-button"),
  downloadButton: document.querySelector("#download-button"),
  qualitySelect: document.querySelector("#quality-select"),
  activeDownloads: document.querySelector("#active-downloads"),
  openLastVideoButton: document.querySelector("#open-last-video-button"),
  openYouTubeStudioButton: document.querySelector("#open-youtube-studio-button"),
  recentLinksList: document.querySelector("#recent-links-list"),
  lastVideo: document.querySelector("#last-video"),
  youtubeForm: document.querySelector("#youtube-form"),
  youtubeUrlInput: document.querySelector("#youtube-url-input"),
  signinBanner: document.querySelector("#signin-banner"),
  openYouTubeButton: document.querySelector("#open-youtube-button"),
  confirmSigninButton: document.querySelector("#confirm-signin-button"),
  switchAccountBannerButton: document.querySelector("#switch-account-banner-button"),
  switchAccountButton: document.querySelector("#switch-account-button"),
  manageYouTubeButton: document.querySelector("#manage-youtube-button"),
  setupPanel: document.querySelector("#setup-panel"),
  setupDownloadButton: document.querySelector("#setup-download-button"),
  setupRecheckButton: document.querySelector("#setup-recheck-button"),
  setupHelpButton: document.querySelector("#setup-help-button"),
  setupPlatformNote: document.querySelector("#setup-platform-note"),
  setupStatus: document.querySelector("#setup-status"),
  mainPanel: document.querySelector("#main-panel"),
};

// --- Platform detection ---

function detectPlatform() {
  const ua = (navigator.userAgentData?.platform || navigator.platform || "").toLowerCase();
  if (ua.includes("mac")) return "mac";
  if (ua.includes("win")) return "windows";
  if (ua.includes("linux")) return "linux";
  return "unknown";
}

// --- URL + format validation (client-side, defense in depth) ---

function extractYouTubeVideoId(url) {
  const hostname = url.hostname.replace(/^www\./, "");
  if (hostname === "youtu.be") return url.pathname.split("/").filter(Boolean)[0] || "";
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
  const raw = String(input || "").trim();
  if (!raw) throw new Error("Paste a YouTube URL first.");
  let parsed;
  try { parsed = new URL(raw); }
  catch { throw new Error("Enter a valid URL that starts with http:// or https://."); }
  if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("Only http/https URLs are supported.");
  if (!YOUTUBE_HOSTS.has(parsed.hostname)) throw new Error("That isn't a supported YouTube URL.");
  const videoId = extractYouTubeVideoId(parsed);
  if (!videoId) throw new Error("Couldn't find a YouTube video ID in that URL.");
  const normalized = new URL("https://www.youtube.com/watch");
  normalized.searchParams.set("v", videoId);
  return { url: normalized.toString(), videoId };
}

function validateFormat(value) {
  const f = String(value || "").trim().toLowerCase();
  if (!ALLOWED_FORMATS.has(f)) throw new Error(`Unsupported quality "${value}".`);
  return f;
}

// --- Setup panel (helper detection) ---

async function pingHost() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "ping-host" }, (response) => {
      // Drain lastError if the worker is somehow gone.
      void chrome.runtime.lastError;
      resolve(response || { ok: false, error: "no-response" });
    });
  });
}

function setSetupStatus(text, kind) {
  if (!els.setupStatus) return;
  els.setupStatus.textContent = text || "";
  els.setupStatus.className = "setup-status" + (kind ? ` ${kind}` : "");
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
  els.setupDownloadButton.textContent = labels[plat] || labels.unknown;
  els.setupPlatformNote.textContent = notes[plat] || notes.unknown;
  els.setupPanel.classList.remove("hidden");
  els.mainPanel.classList.add("hidden");
  setSetupStatus(reason === "ping-failed" ? "Helper not detected on this computer." : "");
}

function showMainUI() {
  els.setupPanel.classList.add("hidden");
  els.mainPanel.classList.remove("hidden");
}

async function checkHelperPresence({ silent = false } = {}) {
  if (!silent) setSetupStatus("Checking…");
  const result = await pingHost();
  if (result.ok) { showMainUI(); return result; }
  showSetupPanel("ping-failed");
  return result;
}

function downloadHelperForPlatform() {
  const plat = detectPlatform();
  const url = HELPER_DOWNLOADS[plat] || HELPER_HELP_URL;
  if (!HELPER_REPO || HELPER_REPO.includes("REPO_OWNER")) {
    setSetupStatus(
      "Download URL hasn't been configured yet — open popup.js and set HELPER_REPO before shipping.",
      "error"
    );
    return;
  }
  chrome.tabs.create({ url });
  setSetupStatus("Opened the download in a new tab. Run the installer, then click “check again”.", "");
}

async function recheckHelper() {
  setSetupStatus("Checking…");
  const result = await pingHost();
  if (result.ok) {
    setSetupStatus(`Helper detected — version ${result.version || "?"}. You're all set.`, "success");
    setTimeout(showMainUI, 600);
  } else {
    setSetupStatus(
      "Still not detected. If you just installed it, give it a few seconds, then click again. " +
      "Make sure you ran the installer matching your operating system.",
      "error"
    );
  }
}

// --- Sign-in banner ---

async function refreshSignInBanner() {
  const confirmedAt = await storage.get(STORAGE_KEYS.signInConfirmed, null);
  els.signinBanner.classList.toggle("hidden", Boolean(confirmedAt));
}

async function handleConfirmSignIn() {
  await storage.set(STORAGE_KEYS.signInConfirmed, new Date().toISOString());
  await refreshSignInBanner();
}

// --- Recent links ---

function formatDate(value) {
  if (!value) return "Never";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function formatRecentLinkLabel(entry) {
  if (!entry?.url) return "Never";
  try {
    const url = new URL(entry.url);
    return url.pathname === "/watch" ? (url.searchParams.get("v") || entry.url) : `${url.hostname}${url.pathname}`;
  } catch { return entry.url; }
}

function renderRecentLinks(recentLinks = []) {
  els.recentLinksList.replaceChildren();
  if (!recentLinks.length) {
    const li = document.createElement("li");
    li.className = "empty-state";
    li.textContent = "No videos downloaded yet.";
    els.recentLinksList.append(li);
    els.openLastVideoButton.disabled = true;
    els.lastVideo.textContent = "Never";
    return;
  }
  for (const entry of recentLinks) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.className = "recent-link";
    a.href = entry.url; a.target = "_blank"; a.rel = "noopener noreferrer";
    a.textContent = formatRecentLinkLabel(entry);
    const when = document.createElement("span");
    when.textContent = ` — ${formatDate(entry.openedAt)}`;
    li.append(a, when);
    els.recentLinksList.append(li);
  }
  els.openLastVideoButton.disabled = false;
  els.lastVideo.textContent = formatRecentLinkLabel(recentLinks[0]);
}

async function saveRecentYouTubeLink(entry) {
  const recent = await storage.get(STORAGE_KEYS.recentYouTubeLinks, []);
  const deduped = recent.filter((it) => it.url !== entry.url);
  await storage.set(STORAGE_KEYS.recentYouTubeLinks, [entry, ...deduped].slice(0, MAX_RECENT_YOUTUBE_LINKS));
}

async function refreshRecentLinks() {
  renderRecentLinks(await storage.get(STORAGE_KEYS.recentYouTubeLinks, []));
}

async function handleYouTubeOpen(event) {
  event.preventDefault();
  const entry = validateAndNormalizeYouTubeUrl(els.youtubeUrlInput.value);
  await chrome.tabs.create({ url: entry.url });
}

async function handleOpenLastVideo() {
  const recent = await storage.get(STORAGE_KEYS.recentYouTubeLinks, []);
  const [last] = recent;
  if (!last?.url) throw new Error("No saved video yet.");
  await chrome.tabs.create({ url: last.url });
}

// --- Download cards (driven by worker broadcasts) ---

const cards = new Map();   // jobId -> { card, els: { ... } }

function makeCard(job) {
  const card = document.createElement("div");
  card.className = "download-card";
  card.dataset.jobId = job.id;

  const header = document.createElement("div");
  header.className = "download-panel-header";
  const filename = document.createElement("span");
  filename.className = "download-filename";
  const percent = document.createElement("span");
  percent.className = "download-percent";
  header.append(filename, percent);

  const track = document.createElement("div");
  track.className = "progress-track";
  const fill = document.createElement("div");
  fill.className = "progress-fill";
  track.append(fill);

  const meta = document.createElement("div");
  meta.className = "download-meta";
  const speed = document.createElement("span");
  const eta = document.createElement("span");
  meta.append(speed, eta);

  const errorBox = document.createElement("div");
  errorBox.className = "download-error hidden";
  const errorIcon = document.createElement("span");
  errorIcon.className = "download-error-icon";
  errorIcon.textContent = "⚠";
  const errorText = document.createElement("div");
  errorText.className = "download-error-text";
  errorBox.append(errorIcon, errorText);

  const actions = document.createElement("div");
  actions.className = "download-actions";

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "download-action download-action-cancel";
  cancelBtn.textContent = "Cancel";

  const openBtn = document.createElement("button");
  openBtn.type = "button";
  openBtn.className = "download-action download-action-open hidden";
  openBtn.textContent = "Open file";

  const revealBtn = document.createElement("button");
  revealBtn.type = "button";
  revealBtn.className = "download-action download-action-reveal hidden";
  revealBtn.textContent = "Show in folder";

  const dismissBtn = document.createElement("button");
  dismissBtn.type = "button";
  dismissBtn.className = "download-action download-action-dismiss hidden";
  dismissBtn.textContent = "Dismiss";

  actions.append(cancelBtn, openBtn, revealBtn, dismissBtn);

  card.append(header, track, meta, errorBox, actions);
  els.activeDownloads.prepend(card);

  cancelBtn.addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "cancel-download", jobId: job.id }, () => {
      void chrome.runtime.lastError;
    });
  });
  openBtn.addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "open-file", jobId: job.id }, () => {
      void chrome.runtime.lastError;
    });
  });
  revealBtn.addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "reveal-file", jobId: job.id }, () => {
      void chrome.runtime.lastError;
    });
  });
  dismissBtn.addEventListener("click", () => {
    card.remove();
    cards.delete(job.id);
    chrome.runtime.sendMessage({ type: "clear-finished" }, () => { void chrome.runtime.lastError; });
  });

  return {
    card,
    els: { filename, percent, fill, speed, eta, errorBox, errorText, cancelBtn, openBtn, revealBtn, dismissBtn },
  };
}

function renderJob(job) {
  let entry = cards.get(job.id);
  if (!entry) {
    entry = makeCard(job);
    cards.set(job.id, entry);
  }
  const c = entry.els;
  const card = entry.card;

  c.filename.textContent = job.filename || `${FORMAT_LABELS[job.format] || job.format} — ${job.url.replace(/^https?:\/\//, "")}`;
  c.percent.textContent = `${Math.round(job.percent || 0)}%`;
  c.fill.style.width = `${Math.max(0, Math.min(100, job.percent || 0))}%`;
  c.speed.textContent = job.speed || "";
  c.eta.textContent = job.eta ? `ETA ${job.eta}` : "";

  card.classList.remove("error", "done", "canceled", "running");
  c.errorBox.classList.add("hidden");
  c.errorText.textContent = "";

  c.cancelBtn.classList.add("hidden");
  c.openBtn.classList.add("hidden");
  c.revealBtn.classList.add("hidden");
  c.dismissBtn.classList.add("hidden");
  c.fill.classList.remove("complete");

  if (job.state === "running" || job.state === "starting") {
    card.classList.add("running");
    c.cancelBtn.classList.remove("hidden");
  } else if (job.state === "done") {
    card.classList.add("done");
    c.fill.classList.add("complete");
    c.fill.style.width = "100%";
    c.percent.textContent = "100%";
    if (job.savedPath) {
      c.openBtn.classList.remove("hidden");
      c.revealBtn.classList.remove("hidden");
    }
    c.dismissBtn.classList.remove("hidden");
  } else if (job.state === "error" || job.state === "canceled") {
    card.classList.add(job.state === "error" ? "error" : "canceled");
    c.errorBox.classList.remove("hidden");
    c.errorText.textContent = job.error || "Failed.";
    c.dismissBtn.classList.remove("hidden");
  }
}

function removeJobCard(jobId) {
  const entry = cards.get(jobId);
  if (!entry) return;
  entry.card.remove();
  cards.delete(jobId);
}

// --- Hydrate jobs from the worker on popup open ---

async function hydrateJobsFromWorker() {
  const response = await new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "list-jobs" }, (r) => {
      void chrome.runtime.lastError;
      resolve(r || { ok: false, jobs: [] });
    });
  });
  if (!response.ok) return;
  // Wipe any stale DOM, render in chronological order (newest first).
  els.activeDownloads.replaceChildren();
  cards.clear();
  const list = (response.jobs || []).slice().reverse();
  for (const job of list) renderJob(job);
}

// --- Live broadcasts from the worker ---

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "job-update" || !msg.job) return;
  renderJob(msg.job);
  // Save to recent links on completion.
  if (msg.job.state === "done") {
    saveRecentYouTubeLink({ url: msg.job.url, openedAt: new Date().toISOString() })
      .then(refreshRecentLinks)
      .catch((err) => console.error("Failed to save recent link", err));
  }
});

// --- Start a download (worker does the actual work) ---

function handleYouTubeDownload() {
  let normalized, format;
  try {
    normalized = validateAndNormalizeYouTubeUrl(els.youtubeUrlInput.value);
    format = validateFormat(els.qualitySelect.value);
  } catch (error) {
    // One-shot inline error card for invalid input.
    const fakeJob = {
      id: `inline-${Date.now()}`,
      url: els.youtubeUrlInput.value || "(no URL)",
      format: els.qualitySelect.value || "best",
      formatLabel: FORMAT_LABELS[els.qualitySelect.value] || els.qualitySelect.value,
      state: "error",
      percent: 0,
      error: error.message,
      filename: "Invalid input",
    };
    renderJob(fakeJob);
    return;
  }

  chrome.runtime.sendMessage(
    {
      type: "start-download",
      url: normalized.url,
      format,
      formatLabel: FORMAT_LABELS[format] || format,
    },
    (response) => {
      void chrome.runtime.lastError;
      if (!response?.ok) {
        const fakeJob = {
          id: `inline-${Date.now()}`,
          url: normalized.url,
          format,
          formatLabel: FORMAT_LABELS[format] || format,
          state: "error",
          percent: 0,
          error: response?.error || "Couldn't reach the helper.",
          filename: "Failed to start",
        };
        renderJob(fakeJob);
        return;
      }
      if (response.job) renderJob(response.job);
    }
  );

  els.youtubeUrlInput.value = "";
  els.youtubeUrlInput.focus();
}

// --- Event listeners ---

els.closeButton?.addEventListener("click", () => window.close());

els.youtubeForm.addEventListener("submit", (event) => {
  handleYouTubeOpen(event).catch((error) => console.error("Failed to open URL", error));
});

els.downloadButton.addEventListener("click", () => handleYouTubeDownload());

els.openLastVideoButton.addEventListener("click", () => {
  handleOpenLastVideo().catch((error) => console.error("Failed to open last video", error));
});

els.openYouTubeStudioButton.addEventListener("click", () => {
  chrome.tabs.create({ url: "https://studio.youtube.com/" });
});

els.openYouTubeButton.addEventListener("click", () => {
  chrome.tabs.create({ url: "https://www.youtube.com/" });
});

// --- Switch Google account ---
//
// AccountChooser shows every Google account already added to Chrome and lets
// the user pick one. After they pick, the `continue` parameter sends them to
// youtube.com signed in as that account — so PixelCatch's cookies-from-browser
// read picks up the new session on the next download.
const SWITCH_ACCOUNT_URL = "https://accounts.google.com/AccountChooser?continue=https%3A%2F%2Fwww.youtube.com%2F";

function openSwitchAccount() {
  chrome.tabs.create({ url: SWITCH_ACCOUNT_URL });
}

els.switchAccountBannerButton?.addEventListener("click", openSwitchAccount);
els.switchAccountButton?.addEventListener("click", openSwitchAccount);
els.manageYouTubeButton?.addEventListener("click", () => {
  chrome.tabs.create({ url: "https://www.youtube.com/" });
});

els.confirmSigninButton.addEventListener("click", () => {
  handleConfirmSignIn().catch((error) => console.error("Failed to record sign-in confirmation", error));
});

els.setupDownloadButton.addEventListener("click", downloadHelperForPlatform);
els.setupRecheckButton.addEventListener("click", () => {
  recheckHelper().catch((err) => setSetupStatus("Couldn't run the check. " + (err?.message || ""), "error"));
});
els.setupHelpButton.addEventListener("click", () => chrome.tabs.create({ url: HELPER_HELP_URL }));

// --- Init ---

refreshRecentLinks().catch(console.error);
refreshSignInBanner().catch(console.error);
hydrateJobsFromWorker().catch(console.error);

els.setupPanel.classList.add("hidden");
els.mainPanel.classList.add("hidden");
checkHelperPresence({ silent: true }).catch((error) => {
  console.error("Helper presence check failed", error);
  showSetupPanel("ping-failed");
});
