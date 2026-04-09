# Extension Foundation

Phase 6 scaffold for a Manifest V3 browser extension with Google authentication, a Gmail module, YouTube link handling, and stronger UX and privacy controls.

## Included

- `manifest.json` for MV3
- popup UI in `popup/`
- background service worker in `background/`
- `chrome.storage.local` wrapper in `src/lib/storage.js`
- environment config for Google OAuth client ID in `src/config/env.js`
- Google sign-in flow using `chrome.identity.getAuthToken({ interactive: true })`
- sign-out, token revoke, and reconnect flow
- Gmail module in `src/gmail/`
- background handlers for profile, messages, drafts, labels, and send flows
- normalized Gmail API error handling for expired tokens, insufficient scopes, and quota errors
- YouTube URL launcher in the popup with recent-link history
- compliant YouTube Studio shortcut for creators downloading their own uploads
- clearer auth loading, failure, and token refresh states
- options-page privacy notice

## YouTube Launcher

The popup now supports:

- pasting a YouTube URL
- validating supported YouTube formats
- opening the video in a new tab with `chrome.tabs.create()`
- saving recent links in `chrome.storage.local`
- reopening the last saved video with one click
- opening the official YouTube Studio workflow for creator-owned uploads

Supported formats include common `youtube.com/watch`, `youtu.be`, `shorts`, `live`, and `embed` URLs. The popup stores the five most recent videos and normalizes them to `https://www.youtube.com/watch?v=...`.

## YouTube Download Compliance

The popup now includes an `Open in YouTube Studio` button and helper text that points users to YouTube's official creator workflow for downloading videos they uploaded themselves.

Per YouTube Help, creators can download their own uploaded videos in YouTube Studio by:

1. Signing in to YouTube Studio
2. Opening `Content`
3. Selecting the video menu
4. Choosing `Download`

The extension does not download YouTube videos directly.

## UX And Security

The popup now surfaces:

- signed-in email
- granted scopes
- token refresh status
- last token status check time
- clearer loading and failure messaging

Security posture in this phase:

- raw OAuth access tokens are not persisted to `chrome.storage.local`
- permissions remain narrow and unchanged beyond what the current feature set needs
- the extension now includes a privacy notice in `options/options.html`

## Gmail Module Surface

The background service worker now exposes these runtime message types:

- `gmail:get-profile`
- `gmail:list-messages`
- `gmail:get-message`
- `gmail:send-message`
- `gmail:create-draft`
- `gmail:list-labels`

The Gmail client is implemented in `src/gmail/api.js`, with MIME message helpers in `src/gmail/mime.js`.

## OAuth Scope Model

This extension uses the smallest scope set needed for the current Gmail feature list:

- `https://www.googleapis.com/auth/gmail.readonly`
  Used for `getProfile`, `listMessages`, `getMessage`, and `listLabels`
- `https://www.googleapis.com/auth/gmail.compose`
  Used for `createDraft` and `sendMessage`

This avoids broader scopes like `gmail.modify` and `https://mail.google.com/`.

## Configure Google OAuth For A Chrome Extension

Chrome and Google require a few manual steps outside the repo before the auth flow will work.

### 1. Keep a stable extension ID

1. Zip the extension folder and upload it as an unpublished item in the Chrome Developer Dashboard
2. Open the Package tab and copy the public key
3. Add that value to the `key` field in `manifest.json` if you want the unpacked extension ID to stay stable during development
4. Confirm the unpacked ID in `chrome://extensions` matches the dashboard item ID

### 2. Configure the Google Auth Platform

1. Create or select a Google Cloud project
2. Open Google Auth Platform and fill in the Branding page
3. Set the audience for your app
4. Enable the Gmail API in the API Library
5. Add the Gmail scope your extension uses: `https://www.googleapis.com/auth/gmail.readonly`
6. Add the compose scope used for drafts and send: `https://www.googleapis.com/auth/gmail.compose`

If you use external users or sensitive Gmail scopes in production, Google may require verification before removing the unverified app screen.

### 3. Create OAuth credentials for the extension

1. In Google Auth Platform, open Clients
2. Create a new OAuth client
3. Choose the Chrome Extension application type
4. Enter your extension item ID
5. Copy the generated client ID

### 4. Add the client ID to the extension

Replace `__GOOGLE_OAUTH_CLIENT_ID__` in both files:

- `manifest.json`
- `src/config/env.js`

Example:

```js
const ENV = Object.freeze({
  GOOGLE_OAUTH_CLIENT_ID: "1234567890-abc123def456.apps.googleusercontent.com"
});
```

And in `manifest.json`:

```json
"oauth2": {
  "client_id": "1234567890-abc123def456.apps.googleusercontent.com",
  "scopes": [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose"
  ]
}
```

## Load The Extension

1. Open Chrome and go to `chrome://extensions`
2. Enable Developer mode
3. Click Load unpacked
4. Select this folder

## Test The Auth Flow

1. Open the popup
2. Click `Connect Google`
3. Approve the OAuth prompt
4. Confirm the popup shows a connected Gmail account
5. Click `Sign Out` to clear cached auth state and revoke the token
6. Click `Reconnect` to run the interactive flow again

The popup still tracks open count and stores a small note in local storage, and now also exercises the standard Chrome extension auth flow through the background service worker.

## Gmail Request Examples

Read mailbox metadata:

```js
await chrome.runtime.sendMessage({ type: "gmail:get-profile" });
await chrome.runtime.sendMessage({
  type: "gmail:list-messages",
  payload: { maxResults: 10, query: "label:inbox" }
});
await chrome.runtime.sendMessage({
  type: "gmail:get-message",
  payload: { id: "MESSAGE_ID", format: "full" }
});
await chrome.runtime.sendMessage({ type: "gmail:list-labels" });
```

Create a draft or send a message:

```js
await chrome.runtime.sendMessage({
  type: "gmail:create-draft",
  payload: {
    interactive: true,
    to: "[email protected]",
    subject: "Draft subject",
    bodyText: "Hello from the extension."
  }
});

await chrome.runtime.sendMessage({
  type: "gmail:send-message",
  payload: {
    interactive: true,
    to: "[email protected]",
    subject: "Sent from the extension",
    bodyText: "Hello from the extension."
  }
});
```

Compose actions accept either structured fields like `to`, `subject`, `bodyText`, and `bodyHtml`, or a prebuilt base64url-encoded `raw` MIME message.
