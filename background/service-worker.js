import { storage } from "../src/lib/storage.js";

const STORAGE_KEYS = {
  appState: "appState",
  recentYouTubeLinks: "recentYouTubeLinks"
};

let panelWindowId = null;

// --- Panel window management ---

chrome.action.onClicked.addListener(() => {
  const popupUrl = chrome.runtime.getURL("popup/popup.html");

  if (panelWindowId !== null) {
    chrome.windows.get(panelWindowId, (win) => {
      if (chrome.runtime.lastError || !win) {
        panelWindowId = null;
        openPanel(popupUrl);
      } else {
        chrome.windows.update(panelWindowId, { focused: true });
      }
    });
  } else {
    openPanel(popupUrl);
  }
});

function openPanel(url) {
  chrome.windows.create(
    { url, type: "popup", width: 420, height: 700, focused: true },
    (win) => { panelWindowId = win.id; }
  );
}

chrome.windows.onRemoved.addListener((windowId) => {
  if (windowId === panelWindowId) {
    panelWindowId = null;
  }
});

// --- Message handling ---

async function handleMessage(message) {
  switch (message?.type) {
    case "app:get-status":
      return { ok: true, serviceWorkerActive: true };
    default:
      return { error: "Unsupported message type.", ok: false };
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message)
    .then((response) => sendResponse(response))
    .catch((error) => {
      console.error("Runtime message failed", error);
      sendResponse({ error: error.message || "Unknown error", ok: false });
    });

  return true;
});
