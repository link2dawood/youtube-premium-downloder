import { getGoogleOAuthClientId, getGoogleOAuthScopes, hasGoogleOAuthClientId } from "../src/config/env.js";
import { createGmailClient, GmailApiError } from "../src/gmail/api.js";
import { GMAIL_READONLY_SCOPE } from "../src/gmail/scopes.js";
import { storage } from "../src/lib/storage.js";

const STORAGE_KEYS = {
  appState: "appState",
  authState: "authState"
};

const AUTH_STATUS_SCOPES = [GMAIL_READONLY_SCOPE];

let lastAccessToken = null;

function isUserDeniedAuthError(error) {
  const message = String(error?.message || "").toLowerCase();
  return (
    message.includes("did not approve access") ||
    message.includes("user did not approve") ||
    message.includes("access_denied") ||
    message.includes("canceled") ||
    message.includes("cancelled")
  );
}

function extractToken(authResult) {
  if (!authResult) {
    return null;
  }

  if (typeof authResult === "string") {
    return authResult;
  }

  return authResult.token ?? null;
}

function extractGrantedScopes(authResult, fallbackScopes = getGoogleOAuthScopes()) {
  if (Array.isArray(authResult?.grantedScopes) && authResult.grantedScopes.length > 0) {
    return authResult.grantedScopes;
  }

  return [...fallbackScopes];
}

function createSignedOutState(reason = "signed-out", error = "") {
  return {
    email: "",
    error,
    grantedScopes: [],
    lastAuthenticatedAt: null,
    lastTokenStatusAt: new Date().toISOString(),
    reason,
    status: "signed_out",
    tokenStatus: "signed_out",
    tokenStatusMessage: error || "No Google token is currently active for this extension."
  };
}

async function writeAppState(reason = "startup") {
  const appState = {
    googleOAuthClientId: getGoogleOAuthClientId(),
    googleOAuthConfigured: hasGoogleOAuthClientId(),
    googleOAuthScopes: getGoogleOAuthScopes(),
    lastUpdatedAt: new Date().toISOString(),
    reason
  };

  await storage.set(STORAGE_KEYS.appState, appState);
  return appState;
}

async function writeAuthState(authState) {
  await storage.set(STORAGE_KEYS.authState, authState);
  return authState;
}

async function fetchGmailProfile(token) {
  const response = await fetch("https://gmail.googleapis.com/gmail/v1/users/me/profile", {
    headers: {
      Authorization: `Bearer ${token}`
    }
  });

  if (response.status === 401) {
    await chrome.identity.removeCachedAuthToken({ token });
    throw new Error("The cached Google token expired. Please reconnect.");
  }

  if (!response.ok) {
    throw new Error(`Gmail API request failed with status ${response.status}.`);
  }

  return response.json();
}

async function requestGoogleAuthToken(
  interactive,
  scopes = getGoogleOAuthScopes(),
  { rememberToken = false } = {}
) {
  const authResult = await chrome.identity.getAuthToken({
    enableGranularPermissions: true,
    interactive,
    scopes
  });
  const token = extractToken(authResult);

  if (!token) {
    throw new Error("Chrome Identity did not return an access token.");
  }

  if (rememberToken) {
    lastAccessToken = token;
  }

  return {
    grantedScopes: extractGrantedScopes(authResult, scopes),
    token
  };
}

async function buildConnectedState(token, grantedScopes, reason, tokenStatus = "ready", tokenStatusMessage = "") {
  const gmailProfile = await fetchGmailProfile(token);
  return {
    email: gmailProfile.emailAddress || "",
    error: "",
    grantedScopes,
    lastAuthenticatedAt: new Date().toISOString(),
    lastTokenStatusAt: new Date().toISOString(),
    reason,
    status: "connected",
    tokenStatus,
    tokenStatusMessage: tokenStatusMessage || "Google token is ready for the current requested scopes."
  };
}

async function invalidateAccessToken(
  token,
  reason = "token-expired",
  errorMessage = "Google access token expired or is invalid. Reconnect the extension."
) {
  if (token) {
    try {
      await chrome.identity.removeCachedAuthToken({ token });
    } catch (error) {
      console.warn("Failed to remove cached auth token", error);
    }
  }

  if (!token || lastAccessToken === token) {
    lastAccessToken = null;
  }

  return writeAuthState({
    ...createSignedOutState(reason, errorMessage),
    tokenStatus: "refresh_failed",
    tokenStatusMessage: errorMessage
  });
}

async function resolveAuthState() {
  if (!hasGoogleOAuthClientId()) {
    return writeAuthState(
      createSignedOutState("missing-client-id", "Add your Google OAuth client ID to manifest.json and src/config/env.js.")
    );
  }

  try {
    const previousAuthState = await storage.get(STORAGE_KEYS.authState, null);
    const { grantedScopes, token } = await requestGoogleAuthToken(false, AUTH_STATUS_SCOPES);
    const mergedScopes = Array.from(
      new Set([...(previousAuthState?.grantedScopes || []), ...grantedScopes])
    );
    return writeAuthState(
      await buildConnectedState(
        token,
        mergedScopes,
        "cached-token",
        "ready",
        "Token cache check passed. Chrome can refresh access when needed."
      )
    );
  } catch (error) {
    const message = error?.message || "";
    const reason = message ? "profile-check-failed" : "interactive-required";
    const authState = createSignedOutState(reason, reason === "profile-check-failed" ? message : "");
    authState.tokenStatus = reason === "profile-check-failed" ? "error" : "authorization_required";
    authState.tokenStatusMessage = reason === "profile-check-failed"
      ? message
      : "Reconnect to grant or refresh Google access.";
    return writeAuthState(authState);
  }
}

async function connectGoogleAccount(scopes = AUTH_STATUS_SCOPES) {
  if (!hasGoogleOAuthClientId()) {
    throw new Error("Google OAuth client ID is missing from the extension config.");
  }

  await writeAuthState({
    ...(await storage.get(STORAGE_KEYS.authState, createSignedOutState("loading"))),
    lastTokenStatusAt: new Date().toISOString(),
    tokenStatus: "refreshing",
    tokenStatusMessage: "Requesting Google access from Chrome Identity."
  });

  try {
    const { grantedScopes, token } = await requestGoogleAuthToken(true, scopes, { rememberToken: true });
    return writeAuthState(
      await buildConnectedState(
        token,
        grantedScopes,
        "interactive-login",
        "refreshed",
        "Google access was refreshed through an interactive sign-in."
      )
    );
  } catch (error) {
    if (isUserDeniedAuthError(error)) {
      const authState = {
        ...(await storage.get(STORAGE_KEYS.authState, createSignedOutState("authorization-denied"))),
        email: "",
        error: "Google sign-in was canceled or access was denied.",
        grantedScopes: [],
        lastAuthenticatedAt: null,
        lastTokenStatusAt: new Date().toISOString(),
        reason: "authorization-denied",
        status: "signed_out",
        tokenStatus: "authorization_required",
        tokenStatusMessage: "Google access was not approved. Try Connect again and approve the consent screen."
      };

      await writeAuthState(authState);
      throw new Error("Google sign-in was canceled or access was denied.");
    }

    throw error;
  }
}

async function revokeGoogleToken(token) {
  const revokeUrl = new URL("https://accounts.google.com/o/oauth2/revoke");
  revokeUrl.searchParams.set("token", token);

  const response = await fetch(revokeUrl.toString(), {
    method: "GET"
  });

  if (!response.ok) {
    throw new Error(`Token revoke failed with status ${response.status}.`);
  }
}

async function disconnectGoogleAccount() {
  let revokeError = "";
  let token = lastAccessToken;

  if (!token) {
    const scopeOptions = [getGoogleOAuthScopes(), AUTH_STATUS_SCOPES];

    for (const scopes of scopeOptions) {
      try {
        const authResult = await requestGoogleAuthToken(false, scopes);
        token = authResult.token;
        break;
      } catch (error) {
        token = null;
      }
    }
  }

  if (token) {
    try {
      await revokeGoogleToken(token);
    } catch (error) {
      revokeError = error.message;
      console.warn("Token revoke failed", error);
    }

    try {
      await chrome.identity.removeCachedAuthToken({ token });
    } catch (error) {
      console.warn("Failed to remove cached auth token", error);
    }
  }

  lastAccessToken = null;
  await chrome.identity.clearAllCachedAuthTokens();

  return writeAuthState(createSignedOutState("signed-out", revokeError));
}

async function reconnectGoogleAccount(scopes = getGoogleOAuthScopes()) {
  await disconnectGoogleAccount();
  return connectGoogleAccount(scopes);
}

const gmailClient = createGmailClient({
  getAccessToken: ({ interactive = false, scopes }) => requestGoogleAuthToken(interactive, scopes),
  invalidateAccessToken
});

async function handleMessage(message) {
  switch (message?.type) {
    case "app:get-status": {
      const [appState, authState] = await Promise.all([
        writeAppState("status-check"),
        resolveAuthState()
      ]);

      return {
        appState,
        authState,
        ok: true,
        serviceWorkerActive: true
      };
    }
    case "auth:connect":
      return {
        authState: await connectGoogleAccount(AUTH_STATUS_SCOPES),
        ok: true
      };
    case "auth:reconnect":
      return {
        authState: await reconnectGoogleAccount(getGoogleOAuthScopes()),
        ok: true
      };
    case "auth:sign-out":
      return {
        authState: await disconnectGoogleAccount(),
        ok: true
      };
    case "gmail:get-profile":
      return {
        data: await gmailClient.getProfile(message.payload),
        ok: true
      };
    case "gmail:list-messages":
      return {
        data: await gmailClient.listMessages(message.payload),
        ok: true
      };
    case "gmail:get-message":
      return {
        data: await gmailClient.getMessage(message.payload),
        ok: true
      };
    case "gmail:send-message":
      return {
        data: await gmailClient.sendMessage(message.payload),
        ok: true
      };
    case "gmail:create-draft":
      return {
        data: await gmailClient.createDraft(message.payload),
        ok: true
      };
    case "gmail:list-labels":
      return {
        data: await gmailClient.listLabels(message.payload),
        ok: true
      };
    default:
      return {
        error: "Unsupported message type.",
        ok: false
      };
  }
}

let panelWindowId = null;

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

chrome.runtime.onInstalled.addListener((details) => {
  Promise.all([writeAppState(details.reason), writeAuthState(createSignedOutState(details.reason))]).catch((error) => {
    console.error("Failed to persist install state", error);
  });
});

chrome.runtime.onStartup.addListener(() => {
  Promise.all([writeAppState("startup"), resolveAuthState()]).catch((error) => {
    console.error("Failed to persist startup state", error);
  });
});

chrome.identity.onSignInChanged.addListener(() => {
  resolveAuthState().catch((error) => {
    console.error("Failed to sync auth state after sign-in change", error);
  });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message)
    .then((response) => {
      sendResponse(response);
    })
    .catch(async (error) => {
      if (isUserDeniedAuthError(error)) {
        console.info("Google auth was canceled or denied by the user.");
      } else {
        console.error("Runtime message failed", error);
      }

      if (error instanceof GmailApiError && error.code === "gmail/auth-expired") {
        await invalidateAccessToken(lastAccessToken, "token-expired", error.message);
      }

      sendResponse({
        error: error.message || "Unknown runtime error",
        errorCode: error.code || "runtime/unknown-error",
        errorDetails: error.details || null,
        errorReasons: error.reasons || [],
        errorStatus: error.status || null,
        ok: false,
        serviceWorkerActive: true
      });
    });

  return true;
});
