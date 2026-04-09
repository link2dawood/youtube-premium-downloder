import { createMimeMessage, encodeMessageForGmail } from "./mime.js";
import { GMAIL_COMPOSE_SCOPE, GMAIL_READONLY_SCOPE } from "./scopes.js";

const GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me";
const MAX_RETRY_ATTEMPTS = 2;
const RETRYABLE_REASONS = new Set([
  "backendError",
  "internalError",
  "quotaExceeded",
  "rateLimitExceeded",
  "userRateLimitExceeded"
]);

function sleep(durationMs) {
  return new Promise((resolve) => {
    setTimeout(resolve, durationMs);
  });
}

function parseRetryAfterMs(value) {
  if (!value) {
    return 0;
  }

  const numericSeconds = Number(value);

  if (Number.isFinite(numericSeconds)) {
    return Math.max(numericSeconds * 1000, 0);
  }

  const parsedDate = Date.parse(value);

  if (Number.isNaN(parsedDate)) {
    return 0;
  }

  return Math.max(parsedDate - Date.now(), 0);
}

function getRetryDelayMs(error, attempt) {
  if (error.retryAfterMs > 0) {
    return error.retryAfterMs;
  }

  const baseDelayMs = Math.min(1000 * 2 ** attempt, 4000);
  return baseDelayMs + Math.floor(Math.random() * 250);
}

function buildUrl(path, query = {}) {
  const url = new URL(`${GMAIL_API_BASE_URL}${path}`);

  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }

    if (Array.isArray(value)) {
      for (const item of value) {
        if (item !== undefined && item !== null && item !== "") {
          url.searchParams.append(key, String(item));
        }
      }
      continue;
    }

    url.searchParams.set(key, String(value));
  }

  return url;
}

function createAuthRequiredMessage(requiredScopes) {
  const scopeList = requiredScopes.join(", ");
  return scopeList
    ? `Google authorization is required for Gmail scopes: ${scopeList}. Reconnect the extension and approve the updated access.`
    : "Google authorization is required before calling the Gmail API.";
}

function hasInsufficientScopes(reasons, message) {
  const normalizedMessage = message.toLowerCase();

  return (
    reasons.includes("insufficientPermissions") ||
    normalizedMessage.includes("insufficient authentication scopes") ||
    normalizedMessage.includes("insufficientpermissions")
  );
}

async function parseResponseBody(response) {
  const text = await response.text();

  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch (error) {
    return text;
  }
}

function classifyGmailError({ message, reasons, requiredScopes, retryAfterMs, status }) {
  if (status === 401 || reasons.includes("authError")) {
    return {
      code: "gmail/auth-expired",
      isRetryable: false,
      message: "Google access token expired or is invalid. Reconnect the extension."
    };
  }

  if (status === 403 && hasInsufficientScopes(reasons, message)) {
    return {
      code: "gmail/insufficient-scopes",
      isRetryable: false,
      message: createAuthRequiredMessage(requiredScopes)
    };
  }

  if (status === 403 && reasons.includes("dailyLimitExceeded")) {
    return {
      code: "gmail/quota-daily-exceeded",
      isRetryable: false,
      message: "The Gmail API daily quota was exceeded for this Google Cloud project."
    };
  }

  if (status === 429 || reasons.some((reason) => RETRYABLE_REASONS.has(reason))) {
    return {
      code: "gmail/quota-rate-limit",
      isRetryable: true,
      message: "The Gmail API rate limit was exceeded. Retry the request shortly.",
      retryAfterMs
    };
  }

  if (status >= 500) {
    return {
      code: "gmail/server-error",
      isRetryable: true,
      message: "The Gmail API returned a server error. Retry the request shortly.",
      retryAfterMs
    };
  }

  return {
    code: "gmail/request-failed",
    isRetryable: false,
    message
  };
}

async function createGmailApiError(response, requiredScopes) {
  const details = await parseResponseBody(response);
  const errorPayload = details && typeof details === "object" ? details.error : null;
  const reasons = Array.isArray(errorPayload?.errors)
    ? errorPayload.errors.map((entry) => entry.reason).filter(Boolean)
    : [];
  const retryAfterMs = parseRetryAfterMs(response.headers.get("retry-after"));
  const fallbackMessage = typeof details === "string" ? details : response.statusText;
  const message = errorPayload?.message || fallbackMessage || "Gmail API request failed.";
  const classification = classifyGmailError({
    message,
    reasons,
    requiredScopes,
    retryAfterMs,
    status: response.status
  });

  return new GmailApiError({
    code: classification.code,
    details,
    isRetryable: classification.isRetryable,
    message: classification.message,
    reasons,
    requiredScopes,
    retryAfterMs: classification.retryAfterMs || retryAfterMs,
    status: response.status
  });
}

function normalizeMaxResults(maxResults = 25) {
  const numericValue = Number(maxResults);

  if (!Number.isFinite(numericValue)) {
    return 25;
  }

  return Math.max(1, Math.min(500, Math.trunc(numericValue)));
}

function normalizeRawMessage(input) {
  if (input.raw) {
    return String(input.raw).trim();
  }

  return encodeMessageForGmail(createMimeMessage(input));
}

export class GmailApiError extends Error {
  constructor({
    code,
    details,
    isRetryable,
    message,
    reasons,
    requiredScopes,
    retryAfterMs,
    status
  }) {
    super(message);
    this.name = "GmailApiError";
    this.code = code;
    this.details = details;
    this.isRetryable = isRetryable;
    this.reasons = reasons;
    this.requiredScopes = requiredScopes;
    this.retryAfterMs = retryAfterMs;
    this.status = status;
  }
}

export function createGmailClient({ getAccessToken, invalidateAccessToken }) {
  async function request({
    body,
    interactive = false,
    method = "GET",
    path,
    query,
    requiredScopes = []
  }) {
    for (let attempt = 0; attempt <= MAX_RETRY_ATTEMPTS; attempt += 1) {
      let authResult;

      try {
        authResult = await getAccessToken({
          interactive,
          scopes: requiredScopes
        });
      } catch (error) {
        throw new GmailApiError({
          code: "gmail/auth-required",
          details: {
            cause: error?.message || String(error)
          },
          isRetryable: false,
          message: createAuthRequiredMessage(requiredScopes),
          reasons: [],
          requiredScopes,
          retryAfterMs: 0,
          status: 401
        });
      }

      const token = authResult?.token;

      if (!token) {
        throw new GmailApiError({
          code: "gmail/auth-required",
          details: null,
          isRetryable: false,
          message: createAuthRequiredMessage(requiredScopes),
          reasons: [],
          requiredScopes,
          retryAfterMs: 0,
          status: 401
        });
      }

      const response = await fetch(buildUrl(path, query), {
        body: body ? JSON.stringify(body) : undefined,
        headers: {
          Authorization: `Bearer ${token}`,
          ...(body ? { "Content-Type": "application/json" } : {})
        },
        method
      });

      if (response.ok) {
        return parseResponseBody(response);
      }

      const gmailError = await createGmailApiError(response, requiredScopes);

      if (gmailError.code === "gmail/auth-expired") {
        await invalidateAccessToken(token, "token-expired", gmailError.message);
      }

      if (gmailError.isRetryable && attempt < MAX_RETRY_ATTEMPTS) {
        await sleep(getRetryDelayMs(gmailError, attempt));
        continue;
      }

      throw gmailError;
    }

    throw new GmailApiError({
      code: "gmail/request-failed",
      details: null,
      isRetryable: false,
      message: "Gmail request failed after retry attempts were exhausted.",
      reasons: [],
      requiredScopes: [],
      retryAfterMs: 0,
      status: 500
    });
  }

  return {
    async createDraft(message = {}) {
      const draftMessage = {
        message: {
          raw: normalizeRawMessage(message)
        }
      };

      if (message.threadId) {
        draftMessage.message.threadId = String(message.threadId);
      }

      return request({
        body: draftMessage,
        interactive: Boolean(message.interactive),
        method: "POST",
        path: "/drafts",
        requiredScopes: [GMAIL_COMPOSE_SCOPE]
      });
    },

    async getMessage({ format = "full", id, metadataHeaders } = {}) {
      if (!id) {
        throw new Error("getMessage requires an id.");
      }

      return request({
        path: `/messages/${encodeURIComponent(String(id))}`,
        query: {
          format,
          metadataHeaders
        },
        requiredScopes: [GMAIL_READONLY_SCOPE]
      });
    },

    async getProfile() {
      return request({
        path: "/profile",
        requiredScopes: [GMAIL_READONLY_SCOPE]
      });
    },

    async listLabels() {
      return request({
        path: "/labels",
        requiredScopes: [GMAIL_READONLY_SCOPE]
      });
    },

    async listMessages({
      includeSpamTrash = false,
      labelIds,
      maxResults = 25,
      pageToken,
      q,
      query
    } = {}) {
      return request({
        path: "/messages",
        query: {
          includeSpamTrash,
          labelIds,
          maxResults: normalizeMaxResults(maxResults),
          pageToken,
          q: q || query
        },
        requiredScopes: [GMAIL_READONLY_SCOPE]
      });
    },

    async sendMessage(message) {
      const payload = {
        raw: normalizeRawMessage(message)
      };

      if (message.threadId) {
        payload.threadId = String(message.threadId);
      }

      return request({
        interactive: Boolean(message.interactive),
        body: payload,
        method: "POST",
        path: "/messages/send",
        requiredScopes: [GMAIL_COMPOSE_SCOPE]
      });
    }
  };
}
