"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useAuth } from "./auth/AuthContext";
import { fetchStats, UnauthorizedError, type FocusPattern } from "./development/api";
import { TEACHER_NAME } from "./shared/teacher";
import { useCoinBalance, useCoinsBypassed } from "./shared/Coins";

// AGENT-001 note 5: Clara's pre-session picker, the teacher counterpart to
// TopicScreen. Instead of a topic pool, the cards are the learner's own error
// ledger (fetchStats().focus — worst-first, max 3), so the default question
// is "what have you actually been getting wrong" rather than a blank slate.
// Free text and the "just want to talk" escape hatch mean this screen is
// never a dead end, even for a brand-new user whose ledger is empty.
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// PAY-002: length picker costs (15 coins/exchange, same as tandem).
const TEACHER_EXCHANGES_KEY = "teacher-exchanges-v1";
const TEACHER_EXCHANGE_OPTIONS = [
  { value: 5, label: "Short · 5 · 75 coins" },
  { value: 10, label: "Classic · 10 · 150 coins" },
  { value: 15, label: "Long · 15 · 225 coins" },
] as const;

function readTeacherExchanges(): number {
  try {
    const raw = parseInt(localStorage.getItem(TEACHER_EXCHANGES_KEY) ?? "", 10);
    return raw === 5 || raw === 10 || raw === 15 ? raw : 10;
  } catch {
    return 10;
  }
}

export default function TeacherTopicScreen({
  onStart,
}: {
  onStart: (topic: string, exchanges?: number) => void;
}) {
  const { token, signOut, user } = useAuth();
  // PAY-002: free tier cannot open Clara (developer bypasses).
  const isFreeLocked = user?.tier === "free" && user?.role !== "developer";
  const [teacherExchanges, setTeacherExchanges] = useState<number>(10);
  useEffect(() => {
    const stored = readTeacherExchanges();
    if (stored !== 10) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setTeacherExchanges(stored);
    }
  }, []);
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

        {/* PAY-002: free-tier lock panel — keep topic cards visible above */}
        {isFreeLocked && (
          <div className="rise-in mt-7 rounded-2xl border-[3px] border-ink bg-flag-gold-soft px-6 py-5" style={{ animationDelay: "240ms" }}>
            <p className="font-display text-[16px] font-black text-ink">Clara is a Basic feature</p>
            <p className="mt-1 font-body text-[14px] text-ink-soft">
              Upgrade to chat with Clara — your grammar teacher who explains in English.
            </p>
            <Link
              href="/pricing"
              className="btn-3d mt-4 inline-flex rounded-2xl border-[3px] border-ink bg-white px-5 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink"
              style={inkShadow}
            >
              See pricing →
            </Link>
          </div>
        )}

        {/* PAY-002: length picker (mirrors TopicScreen) — hidden when locked */}
        {!isFreeLocked && (
          <div className="rise-in mt-7 flex items-center gap-3" style={{ animationDelay: "240ms" }}>
            <span className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              Length
            </span>
            <div className="inline-flex overflow-hidden rounded-full border-[3px] border-ink">
              {TEACHER_EXCHANGE_OPTIONS.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    if (value === teacherExchanges) return;
                    setTeacherExchanges(value);
                    try {
                      localStorage.setItem(TEACHER_EXCHANGES_KEY, String(value));
                    } catch {}
                  }}
                  className={`px-4 py-1.5 font-display text-[12px] font-black uppercase tracking-[0.16em] transition-colors ${
                    value === teacherExchanges ? "bg-ink text-white" : "bg-white text-ink hover:text-flag-red"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}
        {!isFreeLocked && <TeacherBalanceNote exchanges={teacherExchanges} />}

        {/* Start — disabled when locked or insufficient coins */}
        <div
          className="rise-in mt-9 flex flex-col items-start gap-4"
          style={{ animationDelay: "260ms" }}
        >
          {isFreeLocked ? (
            <span className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border-[3px] border-ink bg-paper-warm px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-ink-muted sm:w-auto">
              Locked — Basic required
            </span>
          ) : (
            <TeacherStartButton
              effectiveTopic={effectiveTopic}
              exchanges={teacherExchanges}
              onStart={onStart}
            />
          )}

          {!isFreeLocked && (
            <button
              type="button"
              onClick={() => onStart("", teacherExchanges)}
              className="font-body text-[13px] font-bold text-ink-soft hover:text-ink"
            >
              I just want to talk →
            </button>
          )}
        </div>
      </main>
    </div>
  );
}

// PAY-002: balance note below the teacher length picker.
function TeacherBalanceNote({ exchanges }: { exchanges: number }) {
  const bal = useCoinBalance();
  const bypassed = useCoinsBypassed();
  if (!bal) return null;
  const cost = exchanges * 15;
  if (bypassed) {
    return (
      <p className="rise-in mt-3 font-body text-[12px] text-ink-muted" style={{ animationDelay: "250ms" }}>
        🪙 developer · this chat is free
      </p>
    );
  }
  return (
    <p className="rise-in mt-3 font-body text-[12px] text-ink-muted" style={{ animationDelay: "250ms" }}>
      🪙 {bal.balance} coins · this chat costs {cost} coins
      {bal.balance < cost && (
        <>
          {" "}
          — <Link href="/pricing" className="font-bold text-flag-red underline underline-offset-2">Get more coins</Link>
        </>
      )}
    </p>
  );
}

function TeacherStartButton({
  effectiveTopic,
  exchanges,
  onStart,
}: {
  effectiveTopic: string;
  exchanges: number;
  onStart: (topic: string, exchanges?: number) => void;
}) {
  const bal = useCoinBalance();
  // PAY-002: developers are never charged (see useCoinsBypassed) — their
  // balance is frozen, so pricing must never gate their Start button.
  const bypassed = useCoinsBypassed();
  const cost = exchanges * 15;
  const insufficient = !bypassed && bal !== null && bal.balance < cost;
  const disabled = !effectiveTopic || insufficient;
  return (
    <>
      <button
        type="button"
        onClick={() => onStart(effectiveTopic, exchanges)}
        disabled={disabled}
        className="btn-3d inline-flex w-full items-center justify-center gap-2 rounded-2xl border-[3px] border-ink bg-flag-red px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-white disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
        style={inkShadow}
      >
        Start with {TEACHER_NAME}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
      </button>
      {insufficient && (
        <p className="font-body text-[13px] font-semibold text-flag-red-deep">
          Not enough coins — <Link href="/pricing" className="underline underline-offset-2">get more coins</Link> or pick a shorter chat.
        </p>
      )}
    </>
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
