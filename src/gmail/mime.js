const CRLF = "\r\n";
const NON_ASCII_PATTERN = /[^\x20-\x7E]/;

function bytesToBase64(bytes) {
  let binary = "";

  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }

  return btoa(binary);
}

function encodeBase64(value) {
  return bytesToBase64(new TextEncoder().encode(value));
}

function wrapBase64(value, width = 76) {
  const chunks = [];

  for (let index = 0; index < value.length; index += width) {
    chunks.push(value.slice(index, index + width));
  }

  return chunks.join(CRLF);
}

function encodeHeaderValue(value) {
  const normalizedValue = String(value || "").trim();

  if (!normalizedValue) {
    return "";
  }

  if (!NON_ASCII_PATTERN.test(normalizedValue)) {
    return normalizedValue;
  }

  return `=?UTF-8?B?${encodeBase64(normalizedValue)}?=`;
}

function formatAddress(address) {
  if (!address) {
    return "";
  }

  if (typeof address === "string") {
    return address.trim();
  }

  const email = String(address.email || "").trim();
  const name = String(address.name || "").trim();

  if (!email) {
    throw new Error("Email address objects must include an email field.");
  }

  return name ? `${encodeHeaderValue(name)} <${email}>` : email;
}

function formatAddressList(addresses) {
  if (!addresses) {
    return "";
  }

  const normalizedAddresses = Array.isArray(addresses) ? addresses : [addresses];
  return normalizedAddresses
    .map(formatAddress)
    .filter(Boolean)
    .join(", ");
}

function appendHeader(headers, name, value) {
  if (!value) {
    return;
  }

  headers.push(`${name}: ${value}`);
}

function createTextPart(contentType, content) {
  return [
    `Content-Type: ${contentType}; charset=UTF-8`,
    "Content-Transfer-Encoding: base64",
    "",
    wrapBase64(encodeBase64(content || ""))
  ];
}

function normalizeReferences(references) {
  if (!references) {
    return "";
  }

  if (Array.isArray(references)) {
    return references.filter(Boolean).join(" ");
  }

  return String(references).trim();
}

function buildMultipartBody(bodyText, bodyHtml) {
  const boundary = `boundary_${crypto.randomUUID()}`;

  return {
    contentType: `multipart/alternative; boundary="${boundary}"`,
    lines: [
      `--${boundary}`,
      ...createTextPart("text/plain", bodyText),
      `--${boundary}`,
      ...createTextPart("text/html", bodyHtml),
      `--${boundary}--`,
      ""
    ]
  };
}

function buildSinglePartBody(bodyText, bodyHtml) {
  if (bodyHtml) {
    return {
      contentType: "text/html; charset=UTF-8",
      lines: [
        "Content-Transfer-Encoding: base64",
        "",
        wrapBase64(encodeBase64(bodyHtml))
      ]
    };
  }

  return {
    contentType: "text/plain; charset=UTF-8",
    lines: [
      "Content-Transfer-Encoding: base64",
      "",
      wrapBase64(encodeBase64(bodyText || ""))
    ]
  };
}

export function createMimeMessage({
  bcc,
  bodyHtml = "",
  bodyText = "",
  cc,
  from,
  inReplyTo,
  references,
  replyTo,
  subject = "",
  to
}) {
  const headers = [];

  appendHeader(headers, "From", formatAddress(from));
  appendHeader(headers, "To", formatAddressList(to));
  appendHeader(headers, "Cc", formatAddressList(cc));
  appendHeader(headers, "Bcc", formatAddressList(bcc));
  appendHeader(headers, "Reply-To", formatAddress(replyTo));
  appendHeader(headers, "Subject", encodeHeaderValue(subject));
  appendHeader(headers, "In-Reply-To", String(inReplyTo || "").trim());
  appendHeader(headers, "References", normalizeReferences(references));
  appendHeader(headers, "Date", new Date().toUTCString());
  appendHeader(headers, "MIME-Version", "1.0");

  const body = bodyText && bodyHtml
    ? buildMultipartBody(bodyText, bodyHtml)
    : buildSinglePartBody(bodyText, bodyHtml);

  appendHeader(headers, "Content-Type", body.contentType);

  return [...headers, "", ...body.lines].join(CRLF);
}

export function encodeMessageForGmail(message) {
  return encodeBase64(message)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}
