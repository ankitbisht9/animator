/**
 * Every request to our API goes through one wrapper around `fetch` (installed once at start), so the
 * existing lesson and doubt clients work unchanged with accounts:
 * - state-changing requests carry the CSRF token (double-submit: the readable `anim_csrf` cookie);
 * - a 401 from an expired access token refreshes the session once (one refresh at a time, shared by
 *   every request waiting on it) and retries; if that fails, the app is told the session ended.
 * Tokens live in httpOnly cookies: page scripts never see them.
 */

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const NO_RETRY = /^\/api\/v1\/auth\/(login|register|refresh|logout|password\/|verify-email$)/;
export const SESSION_ENDED = "animator:session-ended";

export function csrfToken(): string {
  const m = /(?:^|;\s*)anim_csrf=([^;]+)/.exec(document.cookie);
  return m?.[1] ? decodeURIComponent(m[1]) : "";
}

let refreshing: Promise<boolean> | null = null;

/** Renew the access token (single flight). True when the session is still alive. */
export function refreshSession(nativeFetch: typeof fetch = window.fetch): Promise<boolean> {
  refreshing ??= nativeFetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      window.setTimeout(() => {
        refreshing = null;
      }, 0);
    });
  return refreshing;
}

function pathOf(input: RequestInfo | URL): string | null {
  try {
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
    return url.origin === window.location.origin ? url.pathname : null;
  } catch {
    return null;
  }
}

let installed = false;

export function installHttp(): void {
  if (installed) return;
  installed = true;
  const native = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = pathOf(input);
    if (!path?.startsWith("/api/")) return native(input, init);
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    const withToken = (): RequestInit => {
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      if (UNSAFE.has(method) && !headers.has("x-csrf-token")) {
        const token = csrfToken();
        if (token) headers.set("x-csrf-token", token);
      }
      return { ...init, headers, credentials: "same-origin" };
    };
    const res = await native(input, withToken());
    if (res.status !== 401 || NO_RETRY.test(path)) return res;
    if (!(await refreshSession(native))) {
      window.dispatchEvent(new CustomEvent(SESSION_ENDED));
      return res;
    }
    return native(input, withToken());
  };
}

export class HttpError extends Error {
  constructor(
    message: string,
    public status: number,
    public requestId?: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new HttpError("Cannot reach the server. Check your connection and try again.", 0);
  }
  if (!res.ok) {
    let detail = res.statusText || `Request failed (${res.status})`;
    let rid: string | undefined;
    try {
      const data = (await res.json()) as { detail?: unknown; request_id?: string };
      rid = data.request_id;
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail))
        detail = data.detail
          .map((d: { msg?: string; loc?: unknown[] }) => (d.msg ?? "").replace(/^Value error, /, ""))
          .filter(Boolean)
          .join(" ");
    } catch {
      /* not JSON */
    }
    if (res.status === 429 && !/[.!]$/.test(detail)) detail = `${detail}.`;
    throw new HttpError(detail, res.status, rid ?? res.headers.get("x-request-id") ?? undefined);
  }
  if (res.status === 204 || res.status === 202) {
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }
  return (await res.json()) as T;
}

export const http = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string, body?: unknown) => request<T>("DELETE", path, body),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}
