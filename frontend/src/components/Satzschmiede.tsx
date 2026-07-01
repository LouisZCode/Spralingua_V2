"use client";

import { useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";

// Vocabulary trainer ("Satzschmiede") — placeholder shell. This is the blank
// canvas the production-practice module will grow into: deck → type a sentence
// → strict examiner → spaced review. For now it only proves the route + auth
// guard + navigation, so there's a real place to iterate on the frontend before
// any backend is wired up.
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

export default function Satzschmiede() {
  const { token, ready } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  if (!ready || !token) {
    return null;
  }

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

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center px-6 py-20 text-center">
        <div className="rise-in flex flex-col items-center">
          <div className="grid h-20 w-20 place-items-center rounded-full border-[4px] border-ink bg-flag-gold-soft text-ink shadow-[0_5px_0_var(--color-ink)]">
            {/* pencil glyph — matches the menu card icon */}
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-9 w-9"
            >
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
            </svg>
          </div>
          <p className="mt-6 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            Satzschmiede
          </p>
          <h1 className="mt-3 font-display text-[clamp(28px,5vw,44px)] font-black leading-[1.05] tracking-tight text-ink">
            Vocabulary practice is
            <br />
            under construction.
          </h1>
          <p className="mt-4 max-w-md font-body text-[16px] leading-relaxed text-ink-soft">
            This is the blank canvas for the sentence trainer. We&apos;ll build
            the deck, the examiner, and spaced review right here.
          </p>
          <Link
            href="/practice"
            className="btn-3d mt-8 inline-flex items-center justify-center gap-2 rounded-[24px] border-[3px] border-ink bg-white px-7 py-4 font-display text-[15px] font-black uppercase tracking-[0.16em] text-ink"
            style={inkShadow}
          >
            ← Back to menu
          </Link>
        </div>
      </main>
    </div>
  );
}
