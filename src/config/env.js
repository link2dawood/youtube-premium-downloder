import { GOOGLE_OAUTH_SCOPES } from "../gmail/scopes.js";

const ENV = Object.freeze({
  GOOGLE_OAUTH_CLIENT_ID: "865980709872-7as1atkavuo2plo6l8kubqusnm1rc3et.apps.googleusercontent.com"
});

export function getGoogleOAuthClientId() {
  return ENV.GOOGLE_OAUTH_CLIENT_ID.trim();
}

export function hasGoogleOAuthClientId() {
  const clientId = getGoogleOAuthClientId();
  return clientId.length > 0 && !clientId.includes("__GOOGLE_OAUTH_CLIENT_ID__");
}

export function getEnv() {
  return ENV;
}

export function getGoogleOAuthScopes() {
  return [...GOOGLE_OAUTH_SCOPES];
}
