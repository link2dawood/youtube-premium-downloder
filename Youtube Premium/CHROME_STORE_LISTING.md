# PixelCatch — Chrome Web Store listing copy

Copy/paste these into the Chrome Web Store developer dashboard fields. Character counts noted where Chrome enforces a limit.

---

## Name (Web Store + manifest)

**PixelCatch**

(45-character limit. Current: 10 chars.)

---

## Short description / Summary

Used as the one-line description in the Web Store listing and in `manifest.json`. Hard cap **132 characters**.

> Download YouTube Premium and members-only videos up to 4K, or audio-only, using your signed-in Chrome session.

(Current: 110 chars.)

---

## Category

**Productivity** (primary)

Alternate options: *Tools*, *Photos & Videos* — but Premium downloaders typically sit under Productivity.

---

## Language

**English (United States)**

---

## Full description

Paste this into the "Detailed description" field on the Web Store dashboard.

```
PixelCatch is a no-fluff downloader for YouTube videos — including the ones you actually pay for: YouTube Premium quality streams and channel members-only uploads. It runs everything locally on your Mac through a small yt-dlp helper, so your videos and your account never leave your machine.

WHAT YOU CAN DOWNLOAD
• Standard public videos at 480p, 720p, 1080p, 2K (1440p), or 4K (2160p)
• YouTube Premium 1080p enhanced-bitrate streams (when your Google account has Premium)
• Channel members-only uploads (when you're a paying member of that channel)
• Audio-only m4a tracks for music, podcasts, and lectures

HOW IT WORKS
• Click the toolbar icon to open the panel
• Paste the YouTube URL
• Pick a quality preset
• Click Download — the file lands in ~/Downloads

PixelCatch sends your URL and selected quality to a small Python helper installed on your machine. The helper runs yt-dlp, which uses your existing signed-in Chrome session to authenticate with YouTube. There is no remote server, no analytics, no Google account integration on our side, and no third-party scripts.

PRIVACY & SECURITY
• Local-only. The extension itself makes zero network requests; downloads happen entirely on your machine.
• No identity, no Gmail, no OAuth — the extension does not request the identity permission and never reads your email.
• Strict allow-listing. The popup and the native helper independently validate every URL host, every quality preset, and every download path. Arbitrary download arguments cannot reach yt-dlp.
• Logs live at ~/Library/Logs/com.pixelcatch.downloader.log with file mode 0600 (only you can read them).

REQUIREMENTS
• macOS
• Python 3.8 or newer
• yt-dlp (install with: brew install yt-dlp). Keep it up to date — YouTube changes frequently.
• A signed-in YouTube account in Chrome. For members-only content you need active membership in the channel; for Premium-bitrate streams you need an active YouTube Premium subscription.

INSTALL THE NATIVE HELPER
After loading the extension, open a terminal in the extension folder and run:
    ./native/install.sh <your-extension-id>
You can find your extension ID on chrome://extensions with Developer mode enabled.

NOT WHAT YOU'RE LOOKING FOR?
PixelCatch will not bypass DRM and will not download videos you don't already have legitimate access to as a signed-in user. It is built for downloading content you've paid for — your Premium streams, your members-only channels, your own uploads — and for personal/offline use of public videos.
```

(No hard character limit — Chrome allows up to 16,000 characters, but ~1,500 is the practical sweet spot.)

---

## Search keywords / Tags

Pick 3–5 from this list when the dashboard prompts:

- youtube downloader
- youtube premium
- members only
- 4k video download
- video to mp3
- yt-dlp

---

## Promotional images

| Asset | Required size | Where to find it |
|---|---|---|
| Store icon | 128 × 128 | `icons/store-icon-128.png` |
| Small promo tile | 440 × 280 | `icons/promo-tile-440x280.png` |
| Large promo tile (optional) | 920 × 680 | not generated — let me know if you want one |
| Marquee promo (optional) | 1400 × 560 | not generated — let me know if you want one |
| Screenshots | 1280 × 800 or 640 × 400 | take from your machine after loading the extension |

You'll need at least 1 screenshot to publish. The cleanest one is the popup with a video URL pasted and the quality dropdown open.

---

## Single-sentence pitches (for social, etc.)

Pick one when you tweet/post about it:

- "PixelCatch — download YouTube Premium and members-only videos up to 4K, locally, using your own signed-in Chrome session."
- "Built for paying YouTube viewers: Premium quality and members-only downloads, 4K, audio-only, no remote server."
- "A YouTube downloader that respects what you actually paid for: Premium bitrates, members-only uploads, your cookies, your machine."
