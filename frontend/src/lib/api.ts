// Single source of truth for the backend origin the browser talks to.
//
// NEXT_PUBLIC_* is inlined at BUILD time, so this must reference
// process.env.NEXT_PUBLIC_API_URL literally. Set it per deployment (e.g.
// https://your-backend.up.railway.app); the default keeps local dev working
// with no .env.local entry.
const RAW_API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8765";

// Trailing slash trimmed so `${HTTP_BASE}/auth/google` can't double up.
export const HTTP_BASE = RAW_API_URL.replace(/\/+$/, "");

// WebSocket origin derived from the HTTP one: http->ws, https->wss. Deriving it
// (rather than a second env var) means the schemes can never drift — an https
// API always yields a wss socket, which is what browsers require on an https page.
export const WS_BASE = HTTP_BASE.replace(/^http/, "ws");

// PAY-001: thin authed clients for the Stripe billing routes (payments/routes.py).
// Same Bearer-replay convention as every other drill's api.ts, and the same
// "surface the backend's own detail string" rule ConversationView's /say
// client uses — a fixed generic message would hide the 503 "billing not
// configured" wording the /pricing page keys off of to show its own note.
async function billingRequest(path: string, token: string, init?: RequestInit): Promise<{ url: string }> {
  const res = await fetch(`${HTTP_BASE}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `${path} failed (${res.status})`
    );
  }
  return res.json() as Promise<{ url: string }>;
}

// Starts a Stripe Checkout session for the given tier and returns the
// Stripe-hosted URL to redirect the browser to. Never call this for a user
// already on a paid tier — Stripe would create a second, parallel
// subscription; the /pricing page enforces that, this client doesn't.
export async function createCheckout(
  tier: "basic" | "premium",
  token: string
): Promise<string> {
  const { url } = await billingRequest("/payments/checkout", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tier }),
  });
  return url;
}

// Opens the Stripe-hosted billing portal for the caller's own subscription
// (switch tier, cancel, update payment method). 404s if the caller has no
// subscription yet — i.e. is still on "free".
export async function fetchBillingPortal(token: string): Promise<string> {
  const { url } = await billingRequest("/payments/portal", token);
  return url;
}
