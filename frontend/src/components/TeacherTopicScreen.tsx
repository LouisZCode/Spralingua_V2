"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useAuth } from "./auth/AuthContext";
import { fetchStats, UnauthorizedError, type FocusPattern } from "./development/api";
import { TEACHER_NAME } from "./shared/teacher";

// AGENT-001 note 5: Clara's pre-session picker, the teacher counterpart to
// TopicScreen. Instead of a topic pool, the cards are the learner's own error
// ledger (fetchStats().focus — worst-first, max 3), so the default question
// is "what have you actually been getting wrong" rather than a blank slate.
// Free text and the "just want to talk" escape hatch mean this screen is
// never a dead end, even for a brand-new user whose ledger is empty.
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

export default function TeacherTopicScreen({
  onStart,
}: {
  onStart: (topic: string) => void;
}) {
  const { token, signOut } = useAuth();
  const [focus, setFocus] = useState<FocusPattern[]>([]);
  const [custom, setCustom] = useState<string>("");
  const [selectedCard, setSelectedCard] = useState<FocusPattern | null>(null);

  // Best-effort ledger fetch. A 401 means the token died server-side — same
  // signOut() idiom TandemChat uses for its gloss calls. Any other failure
  // (network blip, 500) is swallowed: the free-text field still works, so
  // the screen must never dead-end on this request.
  useEffect(() => {
    if (!token) return;
    let alive = true;
    fetchStats(token)
      .then((stats) => {
        if (!alive) return;
        setFocus(stats.focus);
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
      });
    return () => {
      alive = false;
    };
  }, [token, signOut]);

  // A selected ledger card wins over free text — same precedence TopicScreen
  // gives its recommendation cards over the custom-topic input. onStart gets
  // the pattern's label (what Clara should hear), never the patternId.
  const effectiveTopic = useMemo(
    () => selectedCard?.label ?? custom.trim(),
    [selectedCard, custom]
  );

  // Worst pattern (index 0, already ranked by the backend) gets the heavier
  // treatment; the rest sit in a normal-weight row below it.
  const [primary, ...rest] = focus;

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
            {TEACHER_NAME} · Grammar Explained
          </p>
          <h1 className="mt-3 font-display text-[clamp(28px,4.6vw,44px)] font-black leading-[1.03] tracking-tight text-ink">
            What don&apos;t you understand?
          </h1>
          <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
            {TEACHER_NAME} explains in English, one thing at a time. Pick a
            structure you&apos;ve been slipping on lately, or ask about
            anything else.
          </p>
        </div>

        {/* Ledger cards — skipped entirely for a new user with no focus
            patterns yet; free text + "just want to talk" carry the screen. */}
        {primary && (
          <div className="rise-in mt-9" style={{ animationDelay: "80ms" }}>
            <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              Your biggest slip lately
            </p>
            <div className="mt-3">
              <FocusCard
                focus={primary}
                selected={selectedCard?.patternId === primary.patternId}
                onSelect={() =>
                  setSelectedCard((prev) =>
                    prev?.patternId === primary.patternId ? null : primary
                  )
                }
                size="large"
              />
            </div>

            {rest.length > 0 && (
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                {rest.map((f) => (
                  <FocusCard
                    key={f.patternId}
                    focus={f}
                    selected={selectedCard?.patternId === f.patternId}
                    onSelect={() =>
                      setSelectedCard((prev) =>
                        prev?.patternId === f.patternId ? null : f
                      )
                    }
                    size="normal"
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Free-text */}
        <div className="rise-in mt-7" style={{ animationDelay: "200ms" }}>
          <label
            htmlFor="teacher-custom-topic"
            className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted"
          >
            Or ask about anything else…
          </label>
          <input
            id="teacher-custom-topic"
            type="text"
            value={custom}
            onChange={(e) => {
              setCustom(e.target.value);
              setSelectedCard(null);
            }}
            placeholder="e.g. why is it 'dem' and not 'den'?"
            maxLength={120}
            className="mt-3 w-full rounded-2xl border-[3px] border-ink bg-white px-5 py-3.5 font-body text-[16px] text-ink placeholder:text-ink-muted focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
          />
        </div>

        {/* Start, plus the low-key escape hatch that preserves today's
            behaviour (Clara asks what you'd like to understand) — the only
            path for a brand-new user with an empty ledger and nothing typed. */}
        <div
          className="rise-in mt-9 flex flex-col items-start gap-4"
          style={{ animationDelay: "260ms" }}
        >
          <button
            type="button"
            onClick={() => onStart(effectiveTopic)}
            disabled={!effectiveTopic}
            className="btn-3d inline-flex w-full items-center justify-center gap-2 rounded-2xl border-[3px] border-ink bg-flag-red px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-white disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
            style={inkShadow}
          >
            Start with {TEACHER_NAME}
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

          <button
            type="button"
            onClick={() => onStart("")}
            className="font-body text-[13px] font-bold text-ink-soft hover:text-ink"
          >
            I just want to talk →
          </button>
        </div>
      </main>
    </div>
  );
}

function FocusCard({
  focus,
  selected,
  onSelect,
  size,
}: {
  focus: FocusPattern;
  selected: boolean;
  onSelect: () => void;
  size: "large" | "normal";
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={`block w-full rounded-2xl border-[3px] border-ink text-left transition ${
        size === "large" ? "px-6 py-6" : "px-5 py-4"
      } ${
        selected
          ? "bg-ink text-white"
          : "bg-flag-gold-soft text-ink hover:bg-white hover:text-flag-red"
      }`}
      style={inkShadow}
    >
      <div className="flex items-start justify-between gap-3">
        <p
          className={`font-display font-black leading-tight ${
            size === "large" ? "text-[22px]" : "text-[16px]"
          }`}
        >
          {focus.label}
        </p>
        {/* A stale pattern with no recent misses is normal — only show the
            chip when there's something to report, never "0× this week". */}
        {focus.count7d > 0 && (
          <span
            className={`shrink-0 rounded-full border-2 px-2.5 py-0.5 font-body text-[11px] font-bold uppercase tracking-[0.1em] ${
              selected ? "border-white text-white" : "border-ink text-ink"
            }`}
          >
            {focus.count7d}× this week
          </span>
        )}
      </div>
      <p
        className={`mt-1.5 font-body leading-relaxed ${
          size === "large" ? "text-[15px]" : "text-[13px]"
        } ${selected ? "text-white/80" : "text-ink-soft"}`}
      >
        {focus.description}
      </p>
    </button>
  );
}
