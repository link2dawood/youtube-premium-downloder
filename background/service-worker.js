// Background service worker for PixelCatch.
//
// The popup is opened as a free-floating panel window so it can stay visible
// while the user switches tabs. We guard against double-creation when the
// toolbar icon is clicked rapidly.

const PANEL_URL = chrome.runtime.getURL("popup/popup.html");
const PANEL_BOUNDS = { type: "popup", width: 420, height: 700, focused: true };

let panelWindowId = null;
let openInFlight = null;

async function ensurePanel() {
  // Coalesce concurrent clicks behind a single in-flight promise so we never
  // race two windows.create calls while panelWindowId is still null.
  if (openInFlight) {
    return openInFlight;
  }

  openInFlight = (async () => {
    if (panelWindowId !== null) {
      try {
        const win = await chrome.windows.get(panelWindowId);
        if (win) {
          await chrome.windows.update(panelWindowId, { focused: true });
          return panelWindowId;
        }
      } catch {
        // Window was closed externally; fall through and recreate.
        panelWindowId = null;
      }
    }

    const win = await chrome.windows.create({ url: PANEL_URL, ...PANEL_BOUNDS });
    panelWindowId = win.id;
    return panelWindowId;
  })();

  try {
    return await openInFlight;
  } finally {
    openInFlight = null;
  }
}

chrome.action.onClicked.addListener(() => {
  ensurePanel().catch((error) => {
    console.error("Failed to open PixelCatch panel", error);
  });
});

chrome.windows.onRemoved.addListener((windowId) => {
  if (windowId === panelWindowId) {
    panelWindowId = null;
  }
});
