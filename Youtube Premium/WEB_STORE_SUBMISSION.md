# PixelCatch — Chrome Web Store submission checklist

Everything you need to ship the extension. Total time once you start: ~1–2 hours of form-filling, then 1–3 days of Google review.

---

## Pre-flight

### Account + fee

- [ ] Create a Chrome Web Store developer account at https://chrome.google.com/webstore/devconsole/
- [ ] Pay the **one-time $5 registration fee** (per Google account, not per extension)
- [ ] Set up a **publisher profile** (your name or a brand name displayed on the listing)

### Source code

- [ ] On `dev` branch, run a final smoke test of the extension locally
- [ ] Bump `manifest.json` `version` to `"1.0.0"` (or whatever you want as launch)
- [ ] Bump `native/downloader.py` `HELPER_VERSION = "1.0.0"` to match
- [ ] Decide whether to merge `dev` → `main` before publishing (Web Store doesn't care about your branches; this is just for you)

### Helper installer must be live

- [ ] GitHub release `v1.0.0` published with `PixelCatch-Helper.pkg`, `PixelCatch-Helper-Setup.exe`, and `install-linux.sh` attached. The popup's "Download for [OS]" button hits this release.

---

## Build the upload zip

The Web Store wants a single .zip of the extension files (NOT the helper — that ships separately via your GitHub release).

```bash
cd /path/to/extensions
zip -r ../pixelcatch-extension-1.0.0.zip . \
    -x '.git/*' \
    -x 'tools/*' \
    -x 'native/*' \
    -x 'tests/*' \
    -x 'build/*' \
    -x 'dist/*' \
    -x '.github/*' \
    -x 'Youtube Premium/*' \
    -x 'icons/render_icons.py' \
    -x '.DS_Store' \
    -x '*.pyc' \
    -x '__pycache__/*'
```

Resulting zip should contain:

```
manifest.json
background/service-worker.js
popup/popup.html, popup.css, popup.js
options/options.html, options.css
src/lib/storage.js
icons/icon-16.png, icon-32.png, icon-48.png, icon-128.png
README.md (optional but harmless)
```

Verify the zip is under 100 MB (it'll be tiny — under 100 KB).

---

## Listing fields

### Item details

| Field | Value | Notes |
|---|---|---|
| Name | `PixelCatch` | 45-char limit |
| Summary | `Download YouTube Premium and members-only videos up to 4K, or audio-only, using your signed-in Chrome session.` | 132-char limit |
| Description | (paste from `Youtube Premium/CHROME_STORE_LISTING.md`) | 16,000-char limit; ~1,500 is the sweet spot |
| Category | **Productivity** | Alternates: Tools, Photos & Videos |
| Language | English (United States) | |

### Graphic assets

All in your `icons/` directory:

| Asset | Required | File |
|---|---|---|
| Store icon (128×128) | Required | `icons/store-icon-128.png` |
| Small promo tile (440×280) | Required | `icons/promo-tile-440x280.png` |
| Large promo tile (920×680) | Optional | not generated — let me know if you want one |
| Marquee promo (1400×560) | Optional | not generated — needed only if you're nominated for the homepage |
| Screenshots (1280×800 or 640×400) | **At least 1, up to 5** | take from your machine after enabling the extension |

#### Screenshots to take

1. **Side panel open with the download form**, URL pasted, quality dropdown showing 4K — proves the core flow.
2. **Side panel showing 2–3 active downloads in parallel** with progress bars at different percentages — shows the multi-download feature.
3. **Setup panel** with the "Download for macOS" button — shows the onboarding.
4. (Optional) **A completed download** — shows the success state.

Screenshot dimensions are exact: 1280×800 or 640×400. Resize/crop to match.

### Privacy practices form (required)

The Web Store will block submission until you fill this out. PixelCatch's answers:

| Question | Answer |
|---|---|
| Single purpose | "Downloading YouTube videos through a local helper application." |
| Permission justification: `nativeMessaging` | "Communicates with the local PixelCatch Helper, which runs yt-dlp on the user's machine to perform the actual download." |
| Permission justification: `storage` | "Persists the user's recent download URLs in chrome.storage.local so they can be re-opened from the popup." |
| Permission justification: `sidePanel` | "Renders the extension UI in the Chrome side panel so the user can paste URLs and start downloads while continuing to browse." |
| Remote code | **No.** All extension code is included in the package; nothing is fetched or executed at runtime. |
| Data collection | **None.** Check every "we do not collect" box. The extension makes no network requests of its own. |
| Data usage / disclosure / encryption | N/A — no data collected. |
| Privacy policy URL | (Required if you collect anything; since you don't, leave blank or link to your repo's `options/options.html` privacy notice if Google insists.) |

### Distribution

| Field | Value |
|---|---|
| Visibility | **Public** (or **Unlisted** for testing first — Unlisted lets you share a direct link without it appearing in search) |
| Geographic regions | All regions, unless you have a reason to exclude any |
| Mature content | No |
| Pricing | Free |

---

## Things that often trigger Web Store rejection (and how to avoid them)

The Web Store's review team is most likely to push back on these:

- **Missing/vague description.** Use the full description from `CHROME_STORE_LISTING.md` — covers what, how, requirements, privacy.
- **Permissions not justified.** The privacy practices form is where you do this; don't leave any blank.
- **YouTube TOS concern.** Google's policy bans extensions that "violate the rights or terms of services of others." YouTube's TOS prohibits unauthorized downloads. **Frame the listing carefully:** PixelCatch is for users to download Premium content they pay for and members-only content from channels they're members of — i.e. content they're already authorized to access. Avoid generic "download any YouTube video" copy. The current `CHROME_STORE_LISTING.md` is already framed this way.
- **External binary dependency without explanation.** The helper requirement could trigger a reviewer flag. Mitigate by:
  - Naming the helper download URL in the description ("Requires the free open-source PixelCatch Helper, available at https://github.com/link2dawood/youtube-premium-downloder/releases")
  - Including a screenshot of the Setup panel showing the download button.
  - Linking to your README.

If they reject on YouTube TOS grounds, your fallbacks are:
1. Distribute the .zip outside the Web Store via your GitHub releases (users install via "Load unpacked" in chrome://extensions). This is fine for a small / technical user base but loses Web Store's auto-update.
2. Sideload via an enterprise policy (only practical for org deployments).

---

## Submit + review

- [ ] Click **Submit for review** in the developer dashboard.
- [ ] Initial review takes **1–3 business days** typically; longer if there are policy questions.
- [ ] If approved → extension is live at `https://chromewebstore.google.com/detail/<your-extension-id>` and discoverable.
- [ ] If rejected → you get an email with the rationale; fix and resubmit.

The extension ID **changes** when published (Chrome assigns a new permanent ID at first publish). Update the helper's `allowed_origins`:

- [ ] After first publish, copy your new extension ID from the developer dashboard.
- [ ] Update `tools/build-pkg.sh` / `tools/build-win.ps1` invocations to use the production ID — or better, update the `EXTENSION_ID` env var in `.github/workflows/release.yml` so future GitHub Actions builds bake the production ID in.
- [ ] Cut a `v1.0.1` release with the production ID.
- [ ] Old installs of the helper (with the dev extension ID baked in) keep working via their `allowed_origins`; new users get the production ID.

---

## After publication

- [ ] Update the `HELPER_HELP_URL` in `popup/popup.js` to the published Web Store listing URL if you want users to find the listing from the popup.
- [ ] Pin the GitHub release notes referencing both the Web Store URL and the helper download links.
- [ ] Add the Web Store URL to your repo's README badge.

---

## Appendix — minimum viable launch

If you want to ship the absolute minimum first:

1. Build helper installers (already done via Actions).
2. Build the extension .zip (one command above).
3. Upload to Web Store Developer Dashboard.
4. Set visibility to **Unlisted** instead of Public.
5. Submit for review.

Unlisted means it's reviewed but not searchable — you share the direct link with friends to test. Once you're confident in the experience, flip visibility to Public from the dashboard. No re-review needed.
