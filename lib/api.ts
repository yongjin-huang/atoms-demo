import { auth } from "@/auth";

const API_URL = process.env.API_URL ?? "http://localhost:8000";
const INTERNAL_KEY = process.env.INTERNAL_API_KEY ?? "dev-internal-key-change-me";

/**
 * The only path from the browser to the Python service, and it runs on the
 * server. The browser holds a session cookie and nothing else — no token, no
 * API key, no knowledge that a second service exists. Same origin, so no CORS.
 *
 * Header values are percent-encoded: names and avatar URLs are not guaranteed
 * ASCII, and non-ASCII bytes in headers are not portable.
 */
export async function apiFetch(path: string, init: RequestInit = {}) {
  const session = await auth();
  if (!session?.user?.id) return { status: 401 as const, data: { error: "Sign in to build." } };

  const u = session.user;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Internal-Key": INTERNAL_KEY,
    "X-User-Id": u.id,
  };
  if (u.email) headers["X-User-Email"] = encodeURIComponent(u.email);
  if (u.name) headers["X-User-Name"] = encodeURIComponent(u.name);
  if (u.image) headers["X-User-Image"] = encodeURIComponent(u.image);

  try {
    const res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { ...headers, ...(init.headers as Record<string, string>) },
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    return { status: res.status, data };
  } catch {
    // The API is down or unreachable. Say so plainly rather than surfacing a
    // fetch stack trace to the user.
    return { status: 502 as const, data: { error: "The build service is unreachable." } };
  }
}

/** FastAPI reports errors as { detail: "..." }; the UI expects { error: "..." }. */
export function normalise(data: unknown) {
  if (data && typeof data === "object" && "detail" in data) {
    return { error: String((data as { detail: unknown }).detail) };
  }
  return data;
}

/**
 * Streaming variant. Returns the upstream Response untouched so the caller can
 * hand its body straight to the browser — no buffering, no re-encoding. The
 * session check still happens here, server-side, before a single byte moves.
 */
export async function apiStream(path: string, body: string) {
  const session = await auth();
  if (!session?.user?.id) return null;

  const u = session.user;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Internal-Key": INTERNAL_KEY,
    "X-User-Id": u.id,
  };
  if (u.email) headers["X-User-Email"] = encodeURIComponent(u.email);
  if (u.name) headers["X-User-Name"] = encodeURIComponent(u.name);
  if (u.image) headers["X-User-Image"] = encodeURIComponent(u.image);

  return fetch(`${API_URL}${path}`, { method: "POST", headers, body, cache: "no-store" });
}
