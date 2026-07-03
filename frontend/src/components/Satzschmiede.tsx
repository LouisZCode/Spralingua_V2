"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import VocabTrainer from "./satzschmiede/VocabTrainer";
import PackModal from "./satzschmiede/PackModal";
import { fetchDeck, UnauthorizedError } from "./satzschmiede/api";
import type { Card } from "./satzschmiede/deck";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;

// Vocabulary trainer ("Satzschmiede"). The deck is the user's own pool served
// by GET /satz/deck. The trainer is the main surface; packs are browsed and
// added via the "Add a deck" popup (PackModal) — an empty pool shows a CTA
// into that popup instead of cards. This component is the auth-guarded page
// shell + state; VocabTrainer owns the card interaction.
export default function Satzschmiede() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [deck, setDeck] = useState<Card[] | null>(null); // null = loading
  const [error, setError] = useState(false);
  const [packsOpen, setPacksOpen] = useState(false); // the "Add a deck" popup

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  const refreshDeck = useCallback(() => {
    if (!token) return;
    fetchDeck(token)
      .then(setDeck)
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          // Expired session JWT — clear it; the guard above then routes to
          // the landing page for a fresh Google sign-in (AUTH-001, no refresh).
          signOut();
        } else {
          setError(true);
        }
      });
  }, [token, signOut]);

  useEffect(() => {
    refreshDeck();
  }, [refreshDeck]);

  if (!ready || !token) {
    return null;
  }

  const emptyPool = deck !== null && deck.length === 0;
  const label =
    deck === null || error
      ? "Satzschmiede"
      : emptyPool
        ? "Satzschmiede · your pool"
        : `Satzschmiede · your pool · ${deck.length} words`;

  return (
    <div className="relative flex min-h-screen flex-col bg-white text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-2.5">
            <Image
              src="/mascot/raven.png"
              alt="Spralingua raven mascot"
              width={40}
              height={40}
              priority
              className="h-9 w-9 select-none"
            />
            <span className="font-display text-[22px] font-black tracking-tight text-ink">
              Spralingua
            </span>
          </Link>
          <Link
            href="/practice"
            className="font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
          >
            ← Menu
          </Link>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-12">
        <div className="mb-5 flex items-center justify-center gap-4 text-center">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            {label}
          </p>
          {!error && deck !== null && !emptyPool && (
            <button
              type="button"
              onClick={() => setPacksOpen(true)}
              className="font-body text-[11px] font-black uppercase tracking-[0.22em] text-flag-red transition-colors hover:text-flag-red-deep"
            >
              + Add a deck
            </button>
          )}
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            Couldn&apos;t load your deck — is the backend running?
          </p>
        ) : deck === null ? null : emptyPool ? (
          /* First-run state: no cards yet — everything funnels into the popup. */
          <div className="text-center">
            <h2 className="font-display text-[clamp(26px,5vw,36px)] font-black leading-tight tracking-tight text-ink">
              Your pool is empty.
            </h2>
            <p className="mx-auto mt-3 max-w-[360px] font-body text-[15px] leading-relaxed text-ink-soft">
              Add a deck of words — by level or by situation — and start
              forging sentences.
            </p>
            <button
              type="button"
              onClick={() => setPacksOpen(true)}
              className="btn-3d mt-7 inline-flex items-center gap-2 rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3.5 font-display text-[15px] font-black uppercase tracking-[0.16em] text-white"
              style={redShadow}
            >
              + Add a deck
            </button>
          </div>
        ) : (
          <VocabTrainer deck={deck} />
        )}

        {packsOpen && deck !== null && (
          <PackModal
            token={token}
            onPoolChanged={refreshDeck}
            onUnauthorized={signOut}
            onClose={() => setPacksOpen(false)}
            canPractice={deck.length > 0}
          />
        )}
      </main>
    </div>
  );
}
