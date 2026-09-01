"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import AppHeader from "@/components/shared/AppHeader";
import { useAuth } from "@/components/auth/AuthContext";
import { CoinPill, useCoinBalance } from "@/components/shared/Coins";

// Same ink-shadow constant every screen defines locally (no shared visuals
// lib exists — see TopicScreen/Pricing) — kept in that per-file convention.
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// PROFILE-001: "My Profile" — the account affordance the app never had.
// Reached from the avatar circle in the app header (APPHDR-001). This is the
// foundation: identity (picture, name, email), plan (tier + manage on
// /pricing), level (display; the changeable control stays on /practice per
// LEVEL-001), coin balance, and sign-out. Built to grow — future "normal
// profile things" (account settings, stats, data export) belong here.

const TIER_LABELS: Record<string, string> = {
  free: "Free",
  basic: "Basic",
  premium: "Premium",
};

export default function ProfilePage() {
  const { token, ready, user, signOut } = useAuth();
  const router = useRouter();
  const bal = useCoinBalance();

  // Auth guard: wait for localStorage hydration, then bounce signed-out
  // visitors to the landing page — same pattern as the practice screens.
  useEffect(() => {
    if (ready && !token) router.replace("/");
  }, [ready, token, router]);

  if (!ready || !user) {
    return (
      <div className="relative flex min-h-screen flex-col bg-paper text-ink">
        <AppHeader back={{ href: "/practice", label: "← Practice" }} />
        <main className="relative mx-auto flex w-full max-w-3xl flex-1 items-center justify-center px-6">
          <p className="font-body text-[14px] font-semibold text-ink-muted">
            {ready ? "Loading…" : "…"}
          </p>
        </main>
      </div>
    );
  }

  const source = user.name ?? user.email ?? "";
  const initials =
    source
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "·";

  function handleSignOut() {
    signOut();
    router.push("/");
  }

  return (
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <AppHeader back={{ href: "/practice", label: "← Practice" }} />

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col px-6 py-12">
        <div className="rise-in">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            My Profile
          </p>
          <h1 className="mt-3 font-display text-[clamp(28px,4.6vw,44px)] font-black leading-[1.03] tracking-tight text-ink">
            {user.name ?? "Learner"}
          </h1>
          {user.email && (
            <p className="mt-1 font-body text-[15px] text-ink-soft">
              {user.email}
            </p>
          )}
        </div>

        <div
          className="rise-in mt-8 flex items-center gap-5"
          style={{ animationDelay: "60ms" }}
        >
          <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-full border-[3px] border-line bg-paper-warm">
            {user.picture ? (
              <Image
                src={user.picture}
                alt=""
                width={80}
                height={80}
                unoptimized
                className="h-full w-full object-cover"
              />
            ) : (
              <span className="font-display text-[28px] font-black text-ink">
                {initials}
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <span
              className="rounded-full border-[3px] border-line bg-card px-4 py-1.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-ink"
              title="Your plan"
            >
              {TIER_LABELS[user.tier] ?? user.tier} plan
            </span>
            {user.level && (
              <span
                className="rounded-full border-[3px] border-line bg-paper-warm px-4 py-1.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-ink"
                title="Your self-declared level"
              >
                {user.level}
              </span>
            )}
            {bal && (
              <CoinPill balance={bal.balance} nextResetAt={bal.nextResetAt} />
            )}
          </div>
        </div>

        <div
          className="rise-in mt-10 flex flex-col gap-4"
          style={{ animationDelay: "120ms" }}
        >
          <section className="rounded-[28px] border-[3px] border-line bg-card px-6 py-5">
            <h2 className="font-display text-[16px] font-black uppercase tracking-[0.06em] text-ink">
              Plan
            </h2>
            <p className="mt-2 font-body text-[14px] leading-relaxed text-ink-soft">
              You are on the {TIER_LABELS[user.tier] ?? user.tier} plan. Coins
              power every practice mode.
            </p>
            <Link
              href="/pricing"
              className="mt-3 inline-block font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
            >
              {user.tier === "free" ? "Get coins →" : "Manage plan →"}
            </Link>
          </section>

          <section className="rounded-[28px] border-[3px] border-line bg-card px-6 py-5">
            <h2 className="font-display text-[16px] font-black uppercase tracking-[0.06em] text-ink">
              Level
            </h2>
            <p className="mt-2 font-body text-[14px] leading-relaxed text-ink-soft">
              Your level shapes what every mode serves you — and it can change
              whenever you improve.
            </p>
            <Link
              href="/practice"
              className="mt-3 inline-block font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
            >
              Change your level on the practice menu →
            </Link>
          </section>

          <button
            type="button"
            onClick={handleSignOut}
            className="btn-3d mt-4 self-start rounded-[18px] border-[3px] border-line bg-card px-6 py-3 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink"
            style={inkShadow}
          >
            Sign out
          </button>
        </div>
      </main>
    </div>
  );
}

