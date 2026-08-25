// PAY-002: typed clients for the coin + top-up routes.
// Follows lib/api.ts's Bearer-replay + detail-surface conventions.

import { HTTP_BASE } from "./api";

// PAY-002: GET /coins/balance response shape (verified backend contract).
export type CoinBalance = {
  tier: "free" | "basic" | "premium";
  balance: number;
  allowanceRemaining: number;
  purchasedCoins: number;
  dailyAllowance: number;
  nextResetAt: string; // ISO-8601 UTC string
  timezone: string | null;
};

export async function fetchCoinBalance(token: string): Promise<CoinBalance> {
  const res = await fetch(`${HTTP_BASE}/coins/balance`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `/coins/balance failed (${res.status})`
    );
  }
  return res.json() as Promise<CoinBalance>;
}

export async function putTimezone(
  token: string,
  timezone: string
): Promise<{ timezone: string }> {
  const res = await fetch(`${HTTP_BASE}/coins/timezone`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ timezone }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `/coins/timezone failed (${res.status})`
    );
  }
  return res.json() as Promise<{ timezone: string }>;
}

// PAY-002: did THIS checkout session's 500 coins land? Backed by the ledger
// row the webhook writes, so it answers correctly no matter which of the two
// races (browser redirect vs webhook delivery) wins — see
// coins/routes.py::get_topup_status.
export async function fetchTopupCredited(
  token: string,
  checkoutSessionId: string
): Promise<boolean> {
  const res = await fetch(
    `${HTTP_BASE}/coins/topup/${encodeURIComponent(checkoutSessionId)}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `/coins/topup failed (${res.status})`
    );
  }
  const data = (await res.json()) as { credited: boolean };
  return data.credited === true;
}

export async function createTopupCheckout(token: string): Promise<string> {
  const res = await fetch(`${HTTP_BASE}/payments/topup/checkout`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `/payments/topup/checkout failed (${res.status})`
    );
  }
  const data = (await res.json()) as { url: string };
  return data.url;
}

// PAY-002: thrown on 402 insufficient_coins so drill components can render
// OutOfCoinsPanel without plumbing Response bodies through every handler.
export class InsufficientCoinsError extends Error {
  needed: number;
  available: number;
  constructor(needed: number, available: number) {
    super(`Not enough coins (need ${needed}, have ${available})`);
    this.name = "InsufficientCoinsError";
    this.needed = needed;
    this.available = available;
  }
}

function tryParseInsufficientBody(body: unknown): {
  needed: number;
  available: number;
} | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const d = detail as { code?: unknown; needed?: unknown; available?: unknown };
  if (d.code !== "insufficient_coins") return null;
  if (typeof d.needed !== "number" || typeof d.available !== "number") return null;
  return { needed: d.needed, available: d.available };
}

// PAY-002: parses a 402 insufficient_coins body into {needed, available}.
// `resOrBody` may be a Response (reads its cloned body) OR an already-parsed
// JSON body — so both `throw` sites (that have a Response) and `catch` sites
// (that have only an Error + body) can use the same function.
export async function parseInsufficientCoins(
  resOrBody: Response | unknown
): Promise<{ needed: number; available: number } | null> {
  if (resOrBody instanceof Response) {
    if (resOrBody.status !== 402) return null;
    const body = await resOrBody.clone()
      .json()
      .catch(() => null);
    return tryParseInsufficientBody(body);
  }
  return tryParseInsufficientBody(resOrBody);
}

// Synchronous body-object parser — for callers that already awaited res.json().
export function parseInsufficientCoinsBody(body: unknown): {
  needed: number;
  available: number;
} | null {
  return tryParseInsufficientBody(body);
}
