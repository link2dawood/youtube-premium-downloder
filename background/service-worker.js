// Background service worker for PixelCatch.
//
// Single responsibility: tell Chrome that clicking the toolbar icon opens
// the side panel (instead of the legacy popup). The side panel itself is
// declared in manifest.json (`side_panel.default_path`).
//
// We deliberately don't open a separate window anymore — the side panel
// docks to the side of the current Chrome window, persists across tab
// navigation, and is resizable. That's a much better fit for a downloader
// the user wants to keep visible while browsing YouTube.

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.error("Failed to set side panel behavior", error));
});

// Re-apply on startup too, in case onInstalled didn't fire (e.g. a profile
// migration). setPanelBehavior is idempotent.
chrome.runtime.onStartup?.addListener?.(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(() => { /* ignore — non-fatal */ });
});
