"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useSearchParams } from "next/navigation";
import { useAuth } from "./auth/AuthContext";

// PAY-001: lands here after Stripe Checkout redirects back
// (payments/routes.py's success_url is /pricing/success?session_id=...).
//
// Deliberately does NOT fulfill anything client-side — the webhook
// (payments/webhook.py) is the sole source of truth for turning a Checkout
// session into a tier change, and it can land after this page does (Stripe
// delivers the webhook asynchronously; the browser redirect and the webhook
// are two independent races). So this page just polls AuthContext's
// refreshUser() — which re-pulls /auth/me and mirrors the result into
// localStorage — until `tier` moves off "free", or gives up after ~20s and
// tells the learner it can take a minute.
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 20000;

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

type PollStatus = "waiting" | "confirmed" | "timeout";

function tierLabel(tier: string | undefined): string {
  if (tier === "premium") return "Premium";
  if (tier === "basic") return "Basic";
  return "your new plan";
}

export default function PricingSuccess() {
  const { token, ready, user, refreshUser } = useAuth();
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session_id");
  const isTopup = searchParams.get("topup") === "1";

  const [status, setStatus] = useState<PollStatus>("waiting");

  // PAY-002: top-up variant — poll THIS checkout session's ledger row.
  //
  // Not the balance: the two obvious balance tests are both wrong. Watching
  // for a rise misses the (common) case where the webhook credited before
  // this page even mounted, so a real purchase reports a false timeout; and
  // "purchasedCoins > 0" is true for every user alive, since everyone carries
  // the 100-coin signup grant — it confirmed a credit that may never have
  // landed. GET /coins/topup/{id} answers the actual question, in both races.
  //
  // Without a session_id in the URL there is nothing to ask about; fall
  // straight through to the timeout copy ("it can take a minute") rather
  // than claiming success.
  useEffect(() => {
    if (!isTopup) return;
    if (!ready || !token) return;
    if (!sessionId) {
      setStatus("timeout");
      return;
    }
    let cancelled = false;
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    async function pollTopup() {
      try {
        const { fetchTopupCredited } = await import("@/lib/coins");
        const credited = await fetchTopupCredited(token!, sessionId!);
        if (cancelled) return;
        if (credited) {
          setStatus("confirmed");
          // Pull the new balance into the shared store so the coin pill on
          // the next screen is already correct.
          const { refreshCoins } = await import("./shared/Coins");
          refreshCoins();
          return;
        }
      } catch {
        // Network blip or a 5xx — keep polling until the deadline.
      }
      if (Date.now() >= deadline) {
        if (!cancelled) setStatus("timeout");
        return;
      }
      setTimeout(() => {
        if (!cancelled) pollTopup();
      }, POLL_INTERVAL_MS);
    }
    void pollTopup();
    return () => {
      cancelled = true;
    };
  }, [isTopup, ready, token, sessionId]);

  useEffect(() => {
    if (isTopup) return;
    if (!ready || !token) return;
    let cancelled = false;
    const deadline = Date.now() + POLL_TIMEOUT_MS;

    async function poll() {
      const updated = await refreshUser();
      if (cancelled) return;
      if (updated && updated.tier !== "free") {
        setStatus("confirmed");
        return;
      }
      if (Date.now() >= deadline) {
        setStatus("timeout");
        return;
      }
      setTimeout(() => {
        if (!cancelled) poll();
      }, POLL_INTERVAL_MS);
    }
    poll();

    return () => {
      cancelled = true;
    };
  }, [isTopup, ready, token, refreshUser]);

  let body: React.ReactNode;
  if (ready && !token) {
    // Rare: the Stripe redirect landed but the local session is gone
    // (cleared storage, different browser). Nothing to poll against.
    body = (
      <>
        <h1 className="font-display text-[24px] font-black leading-tight text-ink">
          Sign in to see your plan
        </h1>
        <p className="mt-3 font-body text-[15px] leading-relaxed text-ink-soft">
          We couldn&apos;t find your session here. Sign in again and your new
          plan will be waiting for you.
        </p>
        <Link
          href="/"
          className="btn-3d mt-8 inline-flex items-center justify-center rounded-[24px] border-[3px] border-ink bg-card px-7 py-4 font-display text-[15px] font-black uppercase tracking-[0.16em] text-ink"
          style={inkShadow}
        >
          Go to Spralingua
        </Link>
      </>
    );
  } else if (status === "confirmed") {
    if (isTopup) {
      body = (
        <>
          <div className="grid h-16 w-16 place-items-center rounded-full border-[3px] border-success bg-success-soft">
            <CheckIcon />
          </div>
          <h1 className="mt-6 font-display text-[26px] font-black leading-tight text-ink">500 coins added!</h1>
          <p className="mt-3 font-body text-[15px] leading-relaxed text-ink-soft">Your coins are ready — jump back into practice.</p>
          <Link href="/practice" className="btn-3d mt-8 inline-flex items-center justify-center rounded-[24px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-4 font-display text-[15px] font-black uppercase tracking-[0.16em] text-on-fill" style={redShadow}>
            Go to practice →
          </Link>
        </>
      );
    } else {
      body = (
        <>
          <div className="grid h-16 w-16 place-items-center rounded-full border-[3px] border-success bg-success-soft">
            <CheckIcon />
          </div>
          <h1 className="mt-6 font-display text-[26px] font-black leading-tight text-ink">
            You&apos;re on {tierLabel(user?.tier)}!
          </h1>
          <p className="mt-3 font-body text-[15px] leading-relaxed text-ink-soft">
            Your coins are ready — jump back into practice.
          </p>
          <Link
            href="/practice"
            className="btn-3d mt-8 inline-flex items-center justify-center rounded-[24px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-4 font-display text-[15px] font-black uppercase tracking-[0.16em] text-on-fill"
            style={redShadow}
          >
            Go to practice →
          </Link>
        </>
      );
    }
  } else if (status === "timeout") {
    body = (
      <>
        <div className="grid h-16 w-16 place-items-center rounded-full border-[3px] border-ink bg-paper-warm">
          <ClockIcon />
        </div>
        <h1 className="mt-6 font-display text-[24px] font-black leading-tight text-ink">
          Almost there
        </h1>
        <p className="mt-3 font-body text-[15px] leading-relaxed text-ink-soft">
          This can take a minute to finish on our end.{" "}
          {isTopup ? "Your coins" : "Your plan"} will be ready shortly — no
          need to do anything else.
        </p>
        <Link
          href="/practice"
          className="btn-3d mt-8 inline-flex items-center justify-center rounded-[24px] border-[3px] border-ink bg-card px-7 py-4 font-display text-[15px] font-black uppercase tracking-[0.16em] text-ink"
          style={inkShadow}
        >
          Go to practice →
        </Link>
      </>
    );
  } else {
    body = (
      <>
        <div className="grid h-16 w-16 place-items-center rounded-full border-[3px] border-ink bg-flag-gold-soft">
          <span className="inline-block h-7 w-7 animate-spin rounded-full border-[3px] border-ink-faint border-t-ink" />
        </div>
        <h1 className="mt-6 font-display text-[26px] font-black leading-tight text-ink">
          Payment received
        </h1>
        <p className="mt-3 font-body text-[15px] leading-relaxed text-ink-soft">
          Setting up your plan — this only takes a moment…
        </p>
        {sessionId && (
          <p className="mt-6 font-body text-[11px] uppercase tracking-[0.18em] text-ink-faint">
            Session {sessionId.slice(-8)}
          </p>
        )}
      </>
    );
  }

  return (
    <div className="relative flex min-h-screen flex-col bg-card text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <header className="relative border-b-[3px] border-ink bg-card/85 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center px-6 py-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image
              src="/mascot/raven.png"
              alt="Spralingua raven mascot"
              width={36}
              height={36}
              priority
              className="mascot-keyline h-8 w-8 select-none"
            />
            <span className="font-display text-[19px] font-black tracking-tight text-ink">
              Spralingua
            </span>
          </Link>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center px-6 py-16 text-center">
        {body}
      </main>
    </div>
  );
}

function CheckIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-7 w-7 text-success"
    >
      <path d="M20 7 L10 17 L5 12" />
    </svg>
  );
}

function ClockIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-7 w-7 text-ink"
    >
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  );
}
