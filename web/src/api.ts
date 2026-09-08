/**
 * Typed client for the Governed URL Shortener API.
 *
 * The API deliberately has no list endpoint - it exposes create, redirect,
 * stats, health and readiness and nothing else. This client does not invent
 * one; `useLinks` keeps the set of codes this browser created in localStorage
 * instead, so the UI stays a consumer of the existing contract rather than a
 * reason to widen it.
 */

const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export interface Link {
  code: string;
  destination: string;
  created_at: string;
}

export interface ClickEvent {
  occurred_at: string;
}

export interface DailyCount {
  day: string;
  click_count: number;
}

export interface LinkStats {
  code: string;
  destination: string;
  click_count: number;
  recent_clicks: ClickEvent[];
  daily_clicks: DailyCount[];
}

/** An API error carrying the HTTP status, so callers can tell 422 from 404. */
export class ApiError extends Error {
  status: number;
  /** The API's own wording, kept for the developer details panel. */
  raw: string;

  constructor(status: number, message: string, raw?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.raw = raw ?? message;
  }
}

/**
 * FastAPI reports validation failures as a 422 whose `detail` is a list of
 * per-field errors. Those messages say *why* a destination was rejected, so
 * they are extracted rather than replaced with a generic failure.
 */
function readDetail(body: unknown, fallback: string): string {
  if (typeof body !== "object" || body === null) return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry) =>
        typeof entry === "object" && entry !== null && "msg" in entry
          ? String((entry as { msg: unknown }).msg)
          : null,
      )
      .filter((message): message is string => Boolean(message));
    if (messages.length) return messages.join("; ");
  }
  return fallback;
}

/**
 * Turn the API's wording into something a non-technical person can act on.
 *
 * The API is precise but speaks in specification terms - "URL scheme should be
 * 'http' or 'https'" is exactly right and completely unhelpful to someone who
 * has never heard of a scheme. The original text is preserved on `raw` and
 * shown in the developer details, so nothing is hidden, only translated.
 */
function humanize(status: number, apiMessage: string): string {
  const text = apiMessage.toLowerCase();
  if (status === 0) {
    return "Can't reach the service. Make sure it's running (see “For developers” below).";
  }
  if (text.includes("scheme should be") || text.includes("scheme is not")) {
    return "Links need to start with http:// or https://";
  }
  if (text.includes("credentials are not permitted")) {
    return "Remove the username and password from the web address, then try again.";
  }
  if (text.includes("valid url") || text.includes("relative url")) {
    return "That doesn't look like a web address. Try something like https://example.com";
  }
  if (status === 404) return "That short link doesn't exist.";
  if (status === 503) return "The service is running but its database isn't available right now.";
  if (status === 422) return "That web address can't be used. Check it and try again.";
  return apiMessage;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, init);
  } catch {
    // fetch only rejects on a transport failure. That usually means the API is
    // not running - but a CORS misconfiguration looks identical from here, so
    // the message avoids claiming which one it is.
    throw new ApiError(0, humanize(0, ""), `Network request to ${BASE} failed`);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const raw = readDetail(body, `Request failed (${response.status})`);
    throw new ApiError(response.status, humanize(response.status, raw), raw);
  }
  return body as T;
}

/**
 * Accept what people actually type.
 *
 * "example.com" is a perfectly clear intention and the most common way a
 * non-technical user enters a web address, but the API requires an absolute
 * URL. Rather than rejecting it, assume https and report what was assumed so
 * the change is never silent.
 */
export function normalizeDestination(input: string): { url: string; assumedScheme: boolean } {
  const trimmed = input.trim();
  if (!trimmed) return { url: trimmed, assumedScheme: false };
  if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) return { url: trimmed, assumedScheme: false };
  return { url: `https://${trimmed}`, assumedScheme: true };
}

export function createLink(destination: string): Promise<Link> {
  return request<Link>("/links", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ destination }),
  });
}

export function fetchStats(code: string): Promise<LinkStats> {
  return request<LinkStats>(`/links/${encodeURIComponent(code)}/stats`);
}

export async function fetchHealth(): Promise<{ live: boolean; ready: boolean }> {
  const live = await request<{ status: string }>("/health")
    .then(() => true)
    .catch(() => false);
  if (!live) return { live: false, ready: false };
  const ready = await request<{ status: string }>("/ready")
    .then(() => true)
    .catch(() => false);
  return { live, ready };
}

/** The short URL a visitor would actually follow. */
export function shortUrl(code: string): string {
  return `${BASE}/${code}`;
}

/** Just the hostname, for showing a link's target at a glance. */
export function displayHost(destination: string): string {
  try {
    return new URL(destination).hostname.replace(/^www\./, "");
  } catch {
    return destination;
  }
}

export const apiBase = BASE;
export const docsUrl = `${BASE}/docs`;
export const redocUrl = `${BASE}/redoc`;
export const openApiUrl = `${BASE}/openapi.json`;
