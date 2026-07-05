"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { HTTP_BASE } from "@/lib/api";

// The Grammatik-Tandem pre-conversation screen (TANDEM-001, Phase 3 MVP). The
// learner picks what to talk about with Lena: a suggested topic (random pick +
// shuffle), a tap-to-choose chip from the list, or their own free-text theme.
// The chosen string becomes the `topic` param handed to ConversationView.
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

export default function TopicScreen({
  onStart,
}: {
  onStart: (topic: string) => void;
}) {
  const [topics, setTopics] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [custom, setCustom] = useState<string>("");

  // Load the suggestion list once; seed a random highlighted pick so the
  // learner can start in one click. A fetch failure leaves the list empty —
  // the free-text field still works, so the screen is never a dead end.
  useEffect(() => {
    let alive = true;
    fetch(`${HTTP_BASE}/tandem/topics`)
      .then((r) => r.json())
      .then((data: { topics?: string[] }) => {
        if (!alive) return;
        const list = data.topics ?? [];
        setTopics(list);
        if (list.length > 0) {
          setSelected(list[Math.floor(Math.random() * list.length)]);
        }
      })
      .catch(() => {
        /* free-text remains available */
      });
    return () => {
      alive = false;
    };
  }, []);

  // What actually gets sent: a typed theme always wins over a picked chip.
  const effectiveTopic = useMemo(
    () => custom.trim() || selected,
    [custom, selected],
  );

  const shuffle = () => {
    if (topics.length < 2) return;
    // Pick a different one when we can, so the button visibly does something.
    let next = selected;
    for (let i = 0; i < 8 && next === selected; i++) {
      next = topics[Math.floor(Math.random() * topics.length)];
    }
    setSelected(next);
    setCustom("");
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

        {/* Highlighted suggestion + shuffle */}
        <div
          className="rise-in mt-9 rounded-[24px] border-[3px] border-ink bg-flag-gold-soft p-6"
          style={{ ...inkShadow, animationDelay: "80ms" }}
        >
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
            Suggested topic
          </p>
          <div className="mt-2 flex items-center justify-between gap-4">
            <p className="font-display text-[22px] font-black leading-tight text-ink">
              {selected || "Anything you like"}
            </p>
            <button
              type="button"
              onClick={shuffle}
              disabled={topics.length < 2}
              className="btn-3d shrink-0 rounded-full border-[3px] border-ink bg-white px-4 py-2 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink disabled:opacity-40"
              style={inkShadow}
            >
              Shuffle
            </button>
          </div>
        </div>

        {/* All topics as tappable chips */}
        {topics.length > 0 && (
          <div
            className="rise-in mt-7"
            style={{ animationDelay: "140ms" }}
          >
            <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              Or choose another
            </p>
            <div className="mt-3 flex flex-wrap gap-2.5">
              {topics.map((t) => {
                const active = !custom.trim() && t === selected;
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => {
                      setSelected(t);
                      setCustom("");
                    }}
                    className={`rounded-full border-[2px] border-ink px-4 py-2 font-body text-[14px] font-semibold transition ${
                      active
                        ? "bg-ink text-white"
                        : "bg-white text-ink hover:bg-flag-gold-soft"
                    }`}
                  >
                    {t}
                  </button>
                );
              })}
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
