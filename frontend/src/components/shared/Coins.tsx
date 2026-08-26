"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import Link from "next/link";
import { fetchCoinBalance, type CoinBalance } from "@/lib/coins";
import { useAuth } from "../auth/AuthContext";

// PAY-002: shared coin UI — CoinPill, OutOfCoinsPanel and a tiny balance store.

// ─── CoinPill ────────────────────────────────────────────────────────────

export function CoinPill({
  balance,
  nextResetAt,
}: {
  balance: number;
  nextResetAt: string | null;
}) {
  // PAY-002: nextResetAt is a UTC ISO string; render in the USER's browser tz.
  let resetLabel: string | null = null;
  if (nextResetAt) {
    try {
      const d = new Date(nextResetAt);
      if (!isNaN(d.getTime())) {
        resetLabel = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      }
    } catch {
      // ignore
    }
  }

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border-[3px] border-ink bg-card px-3 py-1.5 font-display text-[13px] font-black tracking-[0.08em] text-ink"
      title={
        resetLabel ? `Coins return at ${resetLabel}` : undefined
      }
    >
      <span aria-hidden>🪙</span>
      <span>{balance}</span>
      {resetLabel && (
        <span className="hidden font-body text-[11px] font-semibold text-ink-muted sm:inline">
          · return at {resetLabel}
        </span>
      )}
    </span>
  );
}

// ─── OutOfCoinsPanel ────────────────────────────────────────────────────

export function OutOfCoinsPanel({
  needed,
  available,
  onDismiss,
}: {
  needed: number;
  available: number;
  onDismiss?: () => void;
}) {
  return (
    <div
      role="status"
      className="rounded-2xl border-[3px] border-ink bg-paper-warm px-5 py-4"
    >
      <p className="font-body text-[14px] font-semibold leading-relaxed text-ink">
        You need <span className="font-black">{needed} coins</span> for this,
        you have <span className="font-black">{available}</span>.
      </p>
      <div className="mt-3 flex items-center gap-3">
        <Link
          href="/pricing"
          className="font-body text-[13px] font-bold text-flag-red underline underline-offset-2 hover:text-ink"
        >
          Get more coins →
        </Link>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="font-body text-[12px] font-semibold text-ink-muted hover:text-ink"
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  );
}

// ─── developer bypass ───────────────────────────────────────────────────

// PAY-002: every backend coin gate bypasses `role: "developer"` outright
// (coins/engine.py::try_spend / spend_capped, pipeline/factory.py's accept
// gate) — no charge, no ledger row. So a developer's balance NEVER moves: it
// sits at the 100-coin signup grant forever. Any UI that disables an action
// because the price exceeds the balance must ask this first, or it locks
// developers out of their own product with a balance frozen by design.
export function useCoinsBypassed(): boolean {
  const { user } = useAuth();
  return user?.role === "developer";
}

// ─── tiny balance store ─────────────────────────────────────────────────
// Module-level listener pattern: useCoinBalance subscribes, refreshCoins
// notifies. Keeps it simple — no extra context provider to thread.

let cachedBalance: CoinBalance | null = null;
let cachedToken: string | null = null;
const listeners = new Set<() => void>();
let inFlight: Promise<CoinBalance | null> | null = null;

function notify() {
  for (const l of listeners) l();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function getSnapshot(): CoinBalance | null {
  return cachedBalance;
}

async function doFetch(token: string): Promise<CoinBalance | null> {
  try {
    const bal = await fetchCoinBalance(token);
    cachedBalance = bal;
    cachedToken = token;
    notify();
    return bal;
  } catch {
    return null;
  }
}

export function refreshCoins(): void {
  if (!cachedToken) return;
  // Dedupe concurrent refreshes — one in-flight at a time.
  if (inFlight) return;
  const token = cachedToken;
  inFlight = doFetch(token).finally(() => {
    inFlight = null;
  });
}

// For callers that need to seed the store after sign-in (AuthContext sets
// the token, this primes the cache).
export function setCoinToken(token: string | null): void {
  if (!token) {
    cachedToken = null;
    // Don't clear cachedBalance — a signed-out view just hides the pill.
    return;
  }
  if (token !== cachedToken) {
    cachedToken = token;
    // Fetch balance for the new token — caller may also trigger a focus refetch.
    void doFetch(token);
  }
}

export function useCoinBalance(): CoinBalance | null {
  const { token } = useAuth();

  // Keep the module token in sync — on token change, prime a fetch.
  useEffect(() => {
    if (token) {
      if (token !== cachedToken) {
        cachedToken = token;
        void doFetch(token);
      }
    } else {
      cachedToken = null;
    }
  }, [token]);

  // Refetch on window focus — keeps the pill honest without polling.
  useEffect(() => {
    if (!token) return;
    const onFocus = () => {
      void doFetch(token);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [token]);

  // Subscribe to store updates — useSyncExternalStore for concurrent-mode safety.
  const balance = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  // Also expose a stable refresh callback for drill 402 handlers.
  return balance;
}

// Hook variant that also returns a refresh function for 402 handlers.
export function useCoinBalanceWithRefresh(): {
  balance: CoinBalance | null;
  refresh: () => void;
} {
  const balance = useCoinBalance();
  const refresh = useCallback(() => refreshCoins(), []);
  return { balance, refresh };
}
