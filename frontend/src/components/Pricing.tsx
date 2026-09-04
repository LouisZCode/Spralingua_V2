"use client";

import { useState } from "react";
import { useAuth } from "./auth/AuthContext";
import StartCta from "./auth/StartCta";
import { createCheckout, fetchBillingPortal } from "@/lib/api";
import AppHeader from "@/components/shared/AppHeader";
import { safeMessage } from "@/components/shared/copy";
import {
  SATZ_ATTEMPT_COST,
  LETTER_COST,
  VOICE_EXCHANGE_COST,
  DAILY_ALLOWANCE,
  SIGNUP_GRANT,
  TOPUP_COINS,
  CLARA_DAILY_TALKS,
} from "@/lib/coins";

// PAY-001 (Phase 3): the pricing page. Linked from the landing page nav,
// the landing footer and the practice menu — it's a real, reachable route.
//
// CRITICAL correctness rule this whole file is built around: a signed-in
// user already on "basic" or "premium" must never be sent through Checkout
// again — Stripe would create a second, parallel subscription alongside the
// one the webhook already knows about. Their current tier's card always
// renders as "Current plan" (inert), and the OTHER paid card offers "Switch
// plan" through the billing portal instead of a fresh Checkout session. Only
// a free-tier (or signed-out) visitor ever sees a button that calls
// createCheckout.
type TierId = "free" | "basic" | "premium";

type Plan = {
  id: TierId;
  kicker: string;
  price: string;
  priceNote: string;
  bullets: string[];
};

// PAY-007: every count below comes from the coins.ts mirror of
// coins/prices.py / teacher/routes.py — never retype a price or a daily
// count here.
const PLANS: Plan[] = [
  {
    id: "free",
    kicker: "Free",
    price: "Free",
    priceNote: "",
    bullets: [
      `${DAILY_ALLOWANCE.free} coins a day`,
      `Plus ${SIGNUP_GRANT} coins once, to start — no card needed`,
    ],
  },
  {
    id: "basic",
    kicker: "Basic",
    price: "€15",
    priceNote: "/ month",
    bullets: [
      `${DAILY_ALLOWANCE.basic} coins every day`,
      "20 new words a day",
      "A tandem conversation every day",
      "A letter with corrections",
      `Clara the teacher — ${CLARA_DAILY_TALKS.basic} lesson a day`,
      "Or spend your coins your way",
    ],
  },
  {
    id: "premium",
    kicker: "Premium",
    price: "€25",
    priceNote: "/ month",
    bullets: [
      `${DAILY_ALLOWANCE.premium} coins every day`,
      "30+ new words a day",
      "Long tandem conversations, every day",
      `Clara the teacher — ${CLARA_DAILY_TALKS.premium} lessons a day`,
      "Early access to new features",
    ],
  },
];

const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

export default function Pricing() {
  const { token, user } = useAuth();
  const signedIn = !!token;
  const currentTier: TierId = (user?.tier as TierId) ?? "free";

  // Which button is mid-request — disables the others so a slow network
  // can't produce two in-flight Checkout/portal calls.
  const [pending, setPending] = useState<TierId | "portal" | "topup" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // There's no separate "is billing configured" endpoint to check up front —
  // the only signal is a 503 "billing not configured" from an actual
  // checkout/portal attempt (payments/routes.py::_require_configured). Once
  // any attempt reveals that, stop offering buttons that can only fail the
  // same way and show the note instead.
  const [notConfigured, setNotConfigured] = useState(false);

  async function goCheckout(tier: "basic" | "premium") {
    if (!token) return;
    setError(null);
    setPending(tier);
    try {
      const url = await createCheckout(tier, token);
      window.location.assign(url);
    } catch (e) {
      handleFailure(e);
    }
  }

  async function goPortal() {
    if (!token) return;
    setError(null);
    setPending("portal");
    try {
      const url = await fetchBillingPortal(token);
      window.location.assign(url);
    } catch (e) {
      handleFailure(e);
    }
  }

  function handleFailure(e: unknown) {
    // The "not configured" signal is a deliberate backend detail string
    // (payments/routes.py::_require_configured's 503) — check the raw
    // message for it before running it through safeMessage below, which
    // would otherwise treat an unrecognized string the same as any other
    // detail and just show it (still fine), but this keeps the branch
    // explicit and independent of safeMessage's guard list.
    const raw = e instanceof Error ? e.message : "";
    if (/not configured/i.test(raw)) {
      setNotConfigured(true);
    } else {
      setError(safeMessage(e, "Something went wrong — try again in a moment."));
    }
    setPending(null);
  }

  return (
    <div className="relative min-h-screen bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      {/* APPHDR-001: shared header — auth-aware: signed-in learners come
          back to /practice, visitors to the landing page. */}
      <AppHeader
        logoHref={signedIn ? "/practice" : "/"}
        back={{ href: signedIn ? "/practice" : "/", label: "← Back" }}
      />

      <main className="relative mx-auto max-w-5xl px-6 py-16">
        <div className="rise-in text-center">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            Pricing
          </p>
          <h1 className="mt-3 font-display text-[clamp(30px,5vw,50px)] font-black leading-[1.02] tracking-tight text-ink">
            Coins for however you like to practice.
          </h1>
          {/* PAY-007: what a coin actually buys — the page named "coins"
              nine times without ever defining one. Every number here is
              the lib/coins.ts mirror of coins/prices.py, never retyped. */}
          <p className="mx-auto mt-4 max-w-xl font-body text-[15px] leading-relaxed text-ink-soft">
            A coin is what one piece of practice costs — {SATZ_ATTEMPT_COST}{" "}
            for a word, {LETTER_COST} for a letter, {VOICE_EXCHANGE_COST} for
            a tandem exchange — and every plan tops your coins back up every
            morning at 5&nbsp;am, in your own timezone.
          </p>
          <div className="mt-6 inline-flex flex-col items-start gap-2.5 text-left">
            {[
              "Practice new vocabulary every day",
              "Speak the words you want to learn",
              "Talk to a tandem partner every day",
              "Grammar practice built into real conversations",
              "Answer a letter in your Briefkasten and get it corrected",
            ].map((benefit) => (
              <p
                key={benefit}
                className="flex items-start gap-2 font-body text-[15px] leading-relaxed text-ink-soft"
              >
                <CheckIcon />
                <span>{benefit}</span>
              </p>
            ))}
          </div>
        </div>

        {notConfigured && (
          <div
            className="rise-in mx-auto mt-8 max-w-lg rounded-2xl border-[3px] border-line bg-paper-warm px-5 py-3 text-center font-body text-[13px] font-semibold text-ink-soft"
            style={{ animationDelay: "40ms" }}
          >
            Payments are not live yet — check back soon.
          </div>
        )}
        {error && !notConfigured && (
          <div
            className="rise-in mx-auto mt-8 max-w-lg rounded-2xl border-[3px] border-flag-red bg-flag-red-soft px-5 py-3 text-center font-body text-[13px] font-semibold text-flag-red"
            style={{ animationDelay: "40ms" }}
          >
            {error}
          </div>
        )}

        <div
          className="rise-in mt-10 grid gap-6 sm:grid-cols-3"
          style={{ animationDelay: "80ms" }}
        >
          {PLANS.map((plan) => (
            <PricingCard
              key={plan.id}
              plan={plan}
              signedIn={signedIn}
              currentTier={currentTier}
              pending={pending}
              notConfigured={notConfigured}
              onChoose={goCheckout}
              onSwitch={goPortal}
            />
          ))}
        </div>

        <PlanComparisonTable />

        <TopupBanner
          signedIn={signedIn}
          token={token}
          pending={pending}
          notConfigured={notConfigured}
          onStart={(p) => setPending(p)}
          onDone={() => setPending(null)}
          onFailure={handleFailure}
        />

        {signedIn && currentTier !== "free" && (
          <p
            className="rise-in mt-8 text-center"
            style={{ animationDelay: "140ms" }}
          >
            <button
              type="button"
              onClick={goPortal}
              disabled={pending !== null || notConfigured}
              className="font-body text-[13px] font-semibold text-ink-muted underline underline-offset-2 transition-colors hover:text-flag-red disabled:cursor-default disabled:opacity-50 disabled:hover:text-ink-muted"
            >
              Manage billing
            </button>
          </p>
        )}
      </main>
    </div>
  );
}

function PricingCard({
  plan,
  signedIn,
  currentTier,
  pending,
  notConfigured,
  onChoose,
  onSwitch,
}: {
  plan: Plan;
  signedIn: boolean;
  currentTier: TierId;
  pending: TierId | "portal" | "topup" | null;
  notConfigured: boolean;
  onChoose: (tier: "basic" | "premium") => void;
  onSwitch: () => void;
}) {
  const isCurrent = signedIn && currentTier === plan.id;

  return (
    <div
      className={`flex flex-col rounded-[28px] border-[3px] border-line p-7 ${
        isCurrent ? "bg-flag-gold-soft" : "bg-card"
      }`}
      style={{ boxShadow: "0 5px 0 var(--color-line)" }}
    >
      <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
        {plan.kicker}
        {isCurrent && " · CURRENT"}
      </p>
      <p className="mt-2 font-display text-[36px] font-black leading-none text-ink">
        {plan.price}
        {plan.priceNote && (
          <span className="ml-1.5 font-body text-[14px] font-semibold text-ink-muted">
            {plan.priceNote}
          </span>
        )}
      </p>
      <ul className="mt-6 flex-1 space-y-2.5">
        {plan.bullets.map((b) => (
          <li
            key={b}
            className="flex items-start gap-2 font-body text-[14px] leading-snug text-ink-soft"
          >
            <CheckIcon />
            <span>{b}</span>
          </li>
        ))}
      </ul>
      <div className="mt-6">
        <PricingCta
          plan={plan}
          signedIn={signedIn}
          currentTier={currentTier}
          isCurrent={isCurrent}
          pending={pending}
          notConfigured={notConfigured}
          onChoose={onChoose}
          onSwitch={onSwitch}
        />
      </div>
    </div>
  );
}

const activeBtn =
  "btn-3d w-full rounded-[16px] border-[3px] border-line bg-card px-5 py-3 text-center font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink disabled:cursor-default disabled:opacity-60";
const inertBtn =
  "block w-full rounded-[16px] border-[3px] border-line bg-paper-warm px-5 py-3 text-center font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink-muted";
const noteText =
  "block text-center font-body text-[12px] font-semibold text-ink-muted";

function PricingCta({
  plan,
  signedIn,
  currentTier,
  isCurrent,
  pending,
  notConfigured,
  onChoose,
  onSwitch,
}: {
  plan: Plan;
  signedIn: boolean;
  currentTier: TierId;
  isCurrent: boolean;
  pending: TierId | "portal" | "topup" | null;
  notConfigured: boolean;
  onChoose: (tier: "basic" | "premium") => void;
  onSwitch: () => void;
}) {
  // Signed-out visitor: reuse the app's existing sign-in flow rather than
  // rebuilding it. StartCta opens the Google sign-in modal and lands a
  // successful sign-in on /practice; the visitor comes back here to actually
  // subscribe once they have an account.
  if (!signedIn) {
    return (
      <StartCta className={activeBtn} style={inkShadow}>
        {plan.id === "free" ? "Get started free" : `Sign in for ${plan.kicker}`}
      </StartCta>
    );
  }

  if (plan.id === "free") {
    if (currentTier === "free") {
      return <span className={inertBtn}>Current plan</span>;
    }
    // A paying user has nothing to do on the Free card — no downgrade
    // button here; that's what the billing portal's cancel flow is for.
    return <span className={noteText}>Included with every account</span>;
  }

  if (isCurrent) {
    return <span className={inertBtn}>Current plan</span>;
  }

  if (notConfigured) {
    return <span className={noteText}>Not live yet</span>;
  }

  if (currentTier !== "free") {
    // Already on the OTHER paid tier — route through the portal, never
    // through a second Checkout session.
    const busy = pending === "portal";
    return (
      <button
        type="button"
        onClick={onSwitch}
        disabled={busy}
        className={activeBtn}
        style={inkShadow}
      >
        {busy ? "Opening…" : "Switch plan"}
      </button>
    );
  }

  // Free user choosing a paid tier.
  const busy = pending === plan.id;
  return (
    <button
      type="button"
      onClick={() => {
        if (plan.id !== "free") onChoose(plan.id);
      }}
      disabled={busy}
      className={activeBtn}
      style={inkShadow}
    >
      {busy ? "Starting…" : `Choose ${plan.kicker}`}
    </button>
  );
}

// PAY-007: side-by-side comparison so a visitor doesn't have to cross-read
// three bullet lists to find the two numbers that actually differ between
// plans (the daily coin bucket and Clara's daily lesson count). Static —
// every value comes from the coins.ts mirror, so this renders correctly
// signed out and never touches the balance route.
function PlanComparisonTable() {
  return (
    <div
      className="rise-in mx-auto mt-10 max-w-2xl overflow-x-auto"
      style={{ animationDelay: "100ms" }}
    >
      <table className="w-full min-w-[420px] table-fixed border-collapse overflow-hidden rounded-[20px] border-[3px] border-line bg-card">
        <thead>
          <tr className="border-b-[3px] border-line bg-paper-warm">
            <th className="px-4 py-3 text-left font-display text-[11px] font-black uppercase tracking-[0.14em] text-ink-muted">
              &nbsp;
            </th>
            <th className="px-4 py-3 text-left font-display text-[11px] font-black uppercase tracking-[0.14em] text-ink-muted">
              Free
            </th>
            <th className="px-4 py-3 text-left font-display text-[11px] font-black uppercase tracking-[0.14em] text-ink-muted">
              Basic
            </th>
            <th className="px-4 py-3 text-left font-display text-[11px] font-black uppercase tracking-[0.14em] text-ink-muted">
              Premium
            </th>
          </tr>
        </thead>
        <tbody className="font-body text-[13px] text-ink">
          <tr className="border-b-[2px] border-line">
            <td className="px-4 py-3 font-semibold text-ink-soft">
              Coins every day
            </td>
            <td className="px-4 py-3">
              {DAILY_ALLOWANCE.free}
              <span className="mt-0.5 block font-body text-[11px] font-normal text-ink-muted">
                + {SIGNUP_GRANT} once, to start
              </span>
            </td>
            <td className="px-4 py-3">{DAILY_ALLOWANCE.basic}</td>
            <td className="px-4 py-3">{DAILY_ALLOWANCE.premium}</td>
          </tr>
          <tr>
            <td className="px-4 py-3 font-semibold text-ink-soft">
              Clara lessons a day
            </td>
            <td className="px-4 py-3">{CLARA_DAILY_TALKS.free}</td>
            <td className="px-4 py-3">{CLARA_DAILY_TALKS.basic}</td>
            <td className="px-4 py-3">{CLARA_DAILY_TALKS.premium}</td>
          </tr>
        </tbody>
      </table>
      <p className="mt-3 font-body text-[12px] text-ink-muted">
        Every practice costs the same coins on every plan —{" "}
        {SATZ_ATTEMPT_COST} for a word, {LETTER_COST} for a letter,{" "}
        {VOICE_EXCHANGE_COST} for a tandem exchange. Clara doesn&apos;t cost
        coins — she&apos;s the daily lesson count above. The{" "}
        {SIGNUP_GRANT}-coin welcome grant is one-time, not a daily amount.
      </p>
    </div>
  );
}

// PAY-002: €2 for 500 coins top-up — real button when signed in.
function TopupBanner({
  signedIn,
  token,
  pending,
  notConfigured,
  onStart,
  onDone,
  onFailure,
}: {
  signedIn: boolean;
  token: string | null;
  pending: TierId | "portal" | "topup" | null;
  notConfigured: boolean;
  onStart: (p: "topup") => void;
  onDone: () => void;
  onFailure: (e: unknown) => void;
}) {
  const busy = pending === "topup";
  async function handleTopup() {
    if (!token) return;
    onStart("topup");
    try {
      const { createTopupCheckout } = await import("@/lib/coins");
      const url = await createTopupCheckout(token);
      window.location.assign(url);
    } catch (e) {
      onFailure(e);
      onDone();
    }
  }
  return (
    <div
      className="rise-in mt-6 flex flex-col items-start gap-4 rounded-[28px] border-[3px] border-line bg-paper-warm p-6 sm:flex-row sm:items-center sm:justify-between"
      style={{ boxShadow: "0 5px 0 var(--color-line)", animationDelay: "120ms" }}
    >
      <div>
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">Top-up</p>
        <p className="mt-2 font-display text-[18px] font-bold text-ink">Big day in German? Top up any time.</p>
        <p className="mt-1 font-body text-[14px] text-ink-soft">€2 for a full extra day of coins — {TOPUP_COINS} coins, whenever you need them.</p>
      </div>
      {!signedIn ? (
        <span className="block shrink-0 rounded-[16px] border-[3px] border-line bg-card px-5 py-3 text-center font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink-muted">
          Sign in to top up
        </span>
      ) : notConfigured ? (
        <span className="block shrink-0 rounded-[16px] border-[3px] border-line bg-card px-5 py-3 text-center font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink-muted">
          Not live yet
        </span>
      ) : (
        <button
          type="button"
          onClick={handleTopup}
          disabled={busy || (pending !== null && (pending as string) !== "topup")}
          className="btn-3d shrink-0 rounded-[16px] border-[3px] border-line bg-card px-5 py-3 font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink disabled:opacity-60"
          style={inkShadow}
        >
          {busy ? "Opening…" : "Top up — €2"}
        </button>
      )}
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
      className="mt-0.5 h-4 w-4 shrink-0 text-flag-gold-deep"
    >
      <path d="M20 7 L10 17 L5 12" />
    </svg>
  );
}
