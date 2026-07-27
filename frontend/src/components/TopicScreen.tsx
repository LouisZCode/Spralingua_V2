"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { HTTP_BASE } from "@/lib/api";

// The Grammatik-Tandem pre-conversation screen (TANDEM-001, Phase 3 MVP;
// TAND-005 grew the topic pool and reworked the suggestions below). The
// learner picks what to talk about with Lena: one of 3 randomly recommended
// topics (tap a card to start immediately, or shuffle for a fresh 3), or
// their own free-text theme. The chosen string becomes the `topic` param
// handed to ConversationView.
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// Draw 3 distinct topics from the pool. Only ever called from the mount
// effect below and the shuffle click handler — never during render — so the
// pick can't disagree with the server-rendered HTML (no hydration mismatch;
// same rule Szenario.tsx follows for its own random picks).
function pickThree(pool: string[]): string[] {
  const remaining = [...pool];
  const picks: string[] = [];
  while (remaining.length > 0 && picks.length < 3) {
    const i = Math.floor(Math.random() * remaining.length);
    picks.push(remaining.splice(i, 1)[0]);
  }
  return picks;
}

export default function TopicScreen({
  onStart,
}: {
  onStart: (topic: string) => void;
}) {
  const [topics, setTopics] = useState<string[]>([]);
  const [recommended, setRecommended] = useState<string[]>([]);
  const [custom, setCustom] = useState<string>("");

  // Load the topic pool once; seed today's 3 recommendations from it. A
  // fetch failure leaves both lists empty — the free-text field still works,
  // so the screen is never a dead end.
  useEffect(() => {
    let alive = true;
    fetch(`${HTTP_BASE}/tandem/topics`)
      .then((r) => r.json())
      .then((data: { topics?: string[] }) => {
        if (!alive) return;
        const list = data.topics ?? [];
        setTopics(list);
        setRecommended(pickThree(list));
      })
      .catch(() => {
        /* free-text remains available */
      });
    return () => {
      alive = false;
    };
  }, []);

  // What the free-text Start button sends. The 3 recommendation cards below
  // start the chat directly on tap and don't go through this at all.
  const effectiveTopic = useMemo(() => custom.trim(), [custom]);

  const shuffle = () => {
    setRecommended(pickThree(topics));
  };

  return (
    <div className="relative flex min-h-screen flex-col bg-white text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link href="/practice" className="flex items-center gap-2.5">
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
            className="font-body text-[13px] font-bold text-ink-soft hover:text-ink"
          >
            ← All modes
          </Link>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-14">
        <div className="rise-in">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            Grammatik-Tandem
          </p>
          <h1 className="mt-3 font-display text-[clamp(28px,4.6vw,44px)] font-black leading-[1.03] tracking-tight text-ink">
            What should you and Lena talk about?
          </h1>
          <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
            Pick a topic to break the ice — or bring your own. Lena chats only in
            German and remembers your past conversations.
          </p>
        </div>

        {/* Today's 3 recommended topics + shuffle */}
        {recommended.length > 0 && (
          <div
            className="rise-in mt-9"
            style={{ animationDelay: "80ms" }}
          >
            <div className="flex items-center justify-between gap-4">
              <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
                Our recommendation for you today
              </p>
              <button
                type="button"
                onClick={shuffle}
                disabled={topics.length <= 3}
                className="btn-3d shrink-0 rounded-full border-[3px] border-ink bg-white px-4 py-2 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink disabled:opacity-40"
                style={inkShadow}
              >
                Shuffle
              </button>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              {recommended.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => onStart(t)}
                  className="rounded-2xl border-[3px] border-ink bg-flag-gold-soft px-5 py-5 text-left font-display text-[17px] font-black leading-tight text-ink transition hover:bg-white hover:text-flag-red"
                  style={inkShadow}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Free-text */}
        <div className="rise-in mt-7" style={{ animationDelay: "200ms" }}>
          <label
            htmlFor="tandem-custom-topic"
            className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted"
          >
            Or talk about…
          </label>
          <input
            id="tandem-custom-topic"
            type="text"
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            placeholder="Type your own topic"
            maxLength={120}
            className="mt-3 w-full rounded-2xl border-[3px] border-ink bg-white px-5 py-3.5 font-body text-[16px] text-ink placeholder:text-ink-muted focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
          />
        </div>

        {/* Start */}
        <div className="rise-in mt-9" style={{ animationDelay: "260ms" }}>
          <button
            type="button"
            onClick={() => onStart(effectiveTopic)}
            disabled={!effectiveTopic}
            className="btn-3d inline-flex w-full items-center justify-center gap-2 rounded-2xl border-[3px] border-ink bg-flag-red px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-white disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
            style={inkShadow}
          >
            Start chatting with Lena
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-4 w-4"
            >
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </button>
        </div>
      </main>
    </div>
  );
}
