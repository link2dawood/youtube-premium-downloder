// Background service worker for PixelCatch.
//
// Owns ALL download state. Native messaging ports live here, not in the
// popup, so closing the side panel doesn't kill in-flight downloads. The
// popup is a thin client — it sends "start"/"cancel"/"list" messages,
// subscribes to "job-update" broadcasts, and renders whatever the worker
// reports.
//
// MV3 service workers can be killed when idle. Open native messaging ports
// keep us alive while downloads run; after all downloads finish the worker
// may be torn down. To survive that gracefully we persist the recent-jobs
// snapshot to chrome.storage.session, so re-opening the popup after a
// worker eviction still shows completed downloads.

import { storage } from "../src/lib/storage.js";

const NATIVE_HOST = "com.pixelcatch.downloader";
const MAX_KEPT_JOBS = 20;
const PING_TIMEOUT_MS = 3000;
const JOB_SESSION_KEY = "jobs:snapshot";

// --- In-memory state ---

const jobs = new Map();   // jobId -> Job
let nextJobId = 1;

// --- Side panel ---

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.error("Failed to set side panel behavior", error));
});
chrome.runtime.onStartup?.addListener?.(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(() => { /* non-fatal */ });
});

// --- Job lifecycle ---

function makeJob(opts) {
  const id = String(nextJobId++);
  const job = {
    id,
    url: opts.url,
    format: opts.format,
    formatLabel: opts.formatLabel || opts.format,
    state: "starting",   // starting | running | done | error | canceled
    percent: 0,
    speed: "",
    eta: "",
    filename: "",
    savedPath: "",
    error: "",
    createdAt: Date.now(),
    finishedAt: 0,
  };
  jobs.set(id, job);
  return job;
}

function serializeJob(job) {
  if (!job) return null;
  // Strip the port (not serializable, not needed by popup).
  const { port, ...rest } = job;
  return rest;
}

function broadcastJob(job) {
  const payload = { type: "job-update", job: serializeJob(job) };
  // sendMessage rejects when no listener is open — that's fine, swallow it.
  chrome.runtime.sendMessage(payload).catch(() => {});
  persistJobs();
}

async function persistJobs() {
  try {
    // Persist a serializable snapshot of recent jobs so the popup re-hydrates
    // correctly after the worker is evicted.
    const snapshot = Array.from(jobs.values())
      .map(serializeJob)
      .sort((a, b) => b.createdAt - a.createdAt)
      .slice(0, MAX_KEPT_JOBS);
    await chrome.storage.session.set({ [JOB_SESSION_KEY]: snapshot });
  } catch (err) {
    // chrome.storage.session may not be available in some contexts; that's OK.
  }
}

async function hydrateJobsOnce() {
  if (jobs.size > 0) return; // already populated this lifecycle
  try {
    const snap = await chrome.storage.session.get(JOB_SESSION_KEY);
    const list = snap?.[JOB_SESSION_KEY] || [];
    for (const j of list) {
      // After eviction we lose the live port. Mark anything that wasn't
      // already final as "error" — we can't know the helper's state and
      // shouldn't pretend a download is still in flight.
      if (j.state === "starting" || j.state === "running") {
        j.state = "error";
        j.error = j.error || "Service worker was evicted; download state lost. Please retry.";
        j.finishedAt = Date.now();
      }
      jobs.set(j.id, j);
      const n = Number(j.id);
      if (Number.isFinite(n) && n >= nextJobId) nextJobId = n + 1;
    }
  } catch (err) {
    // ignore
  }
}

// --- Native messaging: download ---

function startDownload(opts) {
  const job = makeJob(opts);

  let port;
  try {
    port = chrome.runtime.connectNative(NATIVE_HOST);
  } catch (err) {
    job.state = "error";
    job.error = friendlyHostError(err?.message || String(err));
    job.finishedAt = Date.now();
    showFailureNotification(job);
    broadcastJob(job);
    return job;
  }

  job.port = port;
  job.state = "running";
  broadcastJob(job);

  port.onMessage.addListener((msg) => onJobMessage(job.id, msg));
  port.onDisconnect.addListener(() => onJobDisconnect(job.id));

  try {
    port.postMessage({
      action: "download",
      url: opts.url,
      format: opts.format,
    });
  } catch (err) {
    job.state = "error";
    job.error = friendlyHostError(err?.message || String(err));
    job.finishedAt = Date.now();
    try { port.disconnect(); } catch { /* already gone */ }
    showFailureNotification(job);
    broadcastJob(job);
  }
  return job;
}

function onJobMessage(jobId, msg) {
  const job = jobs.get(jobId);
  if (!job || !msg) return;

  if (msg.type === "progress") {
    job.state = "running";
    job.percent = Number(msg.percent) || 0;
    job.speed = msg.speed || "";
    job.eta = msg.eta || "";
    if (msg.filename) job.filename = msg.filename;
    broadcastJob(job);
  } else if (msg.type === "done") {
    job.state = "done";
    job.percent = 100;
    if (msg.filename) job.filename = msg.filename;
    job.savedPath = extractSavedPath(msg.message) || "";
    job.finishedAt = Date.now();
    job.error = "";
    showSuccessNotification(job);
    broadcastJob(job);
  } else if (msg.type === "error") {
    job.state = "error";
    job.error = msg.error || "Download failed.";
    job.finishedAt = Date.now();
    showFailureNotification(job);
    broadcastJob(job);
  }
}

function onJobDisconnect(jobId) {
  const job = jobs.get(jobId);
  // Always drain chrome.runtime.lastError to clear Chrome's "unchecked
  // lastError" console warning. The native host always disconnects after
  // a successful exit too, so seeing it here is normal — only treat it as
  // an error if the job was still running.
  const lastError = chrome.runtime.lastError?.message || "";
  if (!job) return;

  if (job.state === "starting" || job.state === "running") {
    job.state = "error";
    job.error = friendlyHostError(lastError || "Helper exited unexpectedly.");
    job.finishedAt = Date.now();
    showFailureNotification(job);
    broadcastJob(job);
  } else {
    // Clean exit after done/error — just persist final state.
    broadcastJob(job);
  }
}

function cancelDownload(jobId) {
  const job = jobs.get(jobId);
  if (!job) return false;
  if (job.state === "done" || job.state === "error" || job.state === "canceled") return false;

  job.state = "canceled";
  job.finishedAt = Date.now();
  job.error = "Canceled by user.";
  try { job.port?.disconnect(); } catch { /* ok */ }
  broadcastJob(job);
  return true;
}

function clearFinishedJobs() {
  let removed = 0;
  for (const [id, job] of jobs) {
    if (job.state === "done" || job.state === "error" || job.state === "canceled") {
      jobs.delete(id);
      removed++;
    }
  }
  persistJobs();
  return removed;
}

// --- Native messaging: ping ---

function pingHost(timeoutMs = PING_TIMEOUT_MS) {
  return new Promise((resolve) => {
    let resolved = false;
    let port;
    const finish = (result) => {
      if (resolved) return;
      resolved = true;
      try { port?.disconnect(); } catch { /* ok */ }
      // Drain any pending lastError from the disconnect.
      void chrome.runtime.lastError;
      resolve(result);
    };

    try {
      port = chrome.runtime.connectNative(NATIVE_HOST);
    } catch (err) {
      finish({ ok: false, error: err?.message || String(err) });
      return;
    }

    const timer = setTimeout(() => finish({ ok: false, error: "timeout" }), timeoutMs);

    port.onMessage.addListener((msg) => {
      if (msg && msg.type === "pong") {
        clearTimeout(timer);
        finish({ ok: true, version: msg.version, platform: msg.platform });
      }
    });

    port.onDisconnect.addListener(() => {
      clearTimeout(timer);
      // Critical: read lastError synchronously to clear the warning.
      const lastError = chrome.runtime.lastError?.message || "disconnected";
      finish({ ok: false, error: lastError });
    });

    try {
      port.postMessage({ action: "ping" });
    } catch (err) {
      clearTimeout(timer);
      finish({ ok: false, error: err?.message || String(err) });
    }
  });
}

// --- Helpers ---

function extractSavedPath(message) {
  if (!message) return "";
  const m = String(message).match(/Saved to (.+?)\.?$/);
  return m ? m[1].trim() : "";
}

function friendlyHostError(rawMessage) {
  const msg = String(rawMessage || "");
  if (/specified native messaging host not found/i.test(msg)) {
    return "PixelCatch Helper isn't installed yet. Install the helper for your operating system and click ‘Check again’.";
  }
  if (/access to the specified native messaging host is forbidden/i.test(msg)) {
    return "Helper rejected this extension's ID. Reinstall the helper with the current extension ID from chrome://extensions.";
  }
  if (/native host has exited/i.test(msg)) {
    return "Helper exited unexpectedly. See ~/Library/Logs/com.pixelcatch.downloader.log on macOS, or the equivalent log on Windows/Linux.";
  }
  return msg || "Unknown native messaging error.";
}

// --- Notifications ---

function showSuccessNotification(job) {
  const notifId = `pc:${job.id}`;
  const message = job.savedPath
    ? `Saved to: ${job.savedPath}`
    : `Saved to your Downloads folder.`;
  chrome.notifications.create(notifId, {
    type: "basic",
    iconUrl: chrome.runtime.getURL("icons/icon-128.png"),
    title: job.filename || "Download complete",
    message,
    contextMessage: `${job.formatLabel} • PixelCatch`,
    buttons: job.savedPath
      ? [{ title: "Open file" }, { title: "Show in folder" }]
      : [],
    requireInteraction: false,
    silent: false,
  });
}

function showFailureNotification(job) {
  const notifId = `pc:${job.id}`;
  // Trim long errors so the OS doesn't crop oddly.
  const message = (job.error || "Download failed.").slice(0, 240);
  chrome.notifications.create(notifId, {
    type: "basic",
    iconUrl: chrome.runtime.getURL("icons/icon-128.png"),
    title: "Download failed",
    message,
    contextMessage: `${job.formatLabel} • PixelCatch`,
    requireInteraction: false,
    silent: false,
  });
}

chrome.notifications.onButtonClicked.addListener((notifId, btnIdx) => {
  if (!notifId.startsWith("pc:")) return;
  const id = notifId.slice(3);
  const job = jobs.get(id);
  if (!job || !job.savedPath) return;

  if (btnIdx === 0) {
    // Open file in the user's default app.
    chrome.tabs.create({ url: `file://${encodeURI(job.savedPath)}` });
  } else if (btnIdx === 1) {
    const dir = job.savedPath.replace(/\/[^/]+$/, "");
    chrome.tabs.create({ url: `file://${encodeURI(dir)}` });
  }
  chrome.notifications.clear(notifId);
});

chrome.notifications.onClicked.addListener((notifId) => {
  // Tapping the notification body opens the file's folder.
  if (!notifId.startsWith("pc:")) return;
  const id = notifId.slice(3);
  const job = jobs.get(id);
  if (job?.savedPath) {
    const dir = job.savedPath.replace(/\/[^/]+$/, "");
    chrome.tabs.create({ url: `file://${encodeURI(dir)}` });
  }
  chrome.notifications.clear(notifId);
});

// --- Message router (popup → worker) ---

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Hydrate from session storage on first call — handles worker eviction.
  hydrateJobsOnce().then(() => routeMessage(msg, sendResponse))
    .catch((err) => sendResponse({ ok: false, error: err?.message || String(err) }));
  return true; // we'll respond asynchronously
});

async function routeMessage(msg, sendResponse) {
  try {
    if (!msg || typeof msg !== "object") {
      return sendResponse({ ok: false, error: "Invalid message." });
    }
    switch (msg.type) {
      case "list-jobs": {
        const list = Array.from(jobs.values())
          .map(serializeJob)
          .sort((a, b) => b.createdAt - a.createdAt);
        return sendResponse({ ok: true, jobs: list });
      }
      case "start-download": {
        const job = startDownload({
          url: msg.url,
          format: msg.format,
          formatLabel: msg.formatLabel,
        });
        return sendResponse({ ok: true, jobId: job.id, job: serializeJob(job) });
      }
      case "cancel-download": {
        const ok = cancelDownload(String(msg.jobId));
        return sendResponse({ ok });
      }
      case "clear-finished": {
        const removed = clearFinishedJobs();
        return sendResponse({ ok: true, removed });
      }
      case "ping-host": {
        const result = await pingHost();
        return sendResponse(result);
      }
      case "reveal-file": {
        const job = jobs.get(String(msg.jobId));
        if (job?.savedPath) {
          const dir = job.savedPath.replace(/\/[^/]+$/, "");
          chrome.tabs.create({ url: `file://${encodeURI(dir)}` });
          return sendResponse({ ok: true });
        }
        return sendResponse({ ok: false, error: "No saved path." });
      }
      case "open-file": {
        const job = jobs.get(String(msg.jobId));
        if (job?.savedPath) {
          chrome.tabs.create({ url: `file://${encodeURI(job.savedPath)}` });
          return sendResponse({ ok: true });
        }
        return sendResponse({ ok: false, error: "No saved path." });
      }
      default:
        return sendResponse({ ok: false, error: `Unknown message type: ${msg.type}` });
    }
  } catch (err) {
    sendResponse({ ok: false, error: err?.message || String(err) });
  }
}
