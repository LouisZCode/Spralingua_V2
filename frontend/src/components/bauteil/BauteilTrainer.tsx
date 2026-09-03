"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { BauteilVerdict, RoundItem } from "./api";
import { FeedbackCard } from "../shared/feedback";
import { playSound } from "../shared/sound";
import { InsufficientCoinsError } from "@/lib/coins";
import { OutOfCoinsPanel, refreshCoins } from "../shared/Coins";

// A missed item returns once at the end of the round — a second chance from
// memory, after the correction has had time to fade. `retry` marks the copy
// so a second miss doesn't requeue it forever.
type QueueItem = RoundItem & { retry?: boolean };

type Phase = "intro" | "drill" | "done";

const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// The drill rules, worded per the GRAM-002 entry ("rules to teach in the
// wording"): the flag rule and the three naked ein-spots, shown once up
// front — the per-miss axis feedback then does the teaching in context.
const RULES: { title: string; body: string }[] = [
  {
    title: "Build the phrase",
    body: "You get raw parts — ein · gut · Job — and a sentence with a gap. Type the parts, bent so they fit the gap.",
  },
  {
    title: "The sentence decides the case",
    body: "Read it first: is the phrase the subject, a direct object, or behind a dative verb or preposition?",
  },
  {
    title: "The flag flies exactly once",
    body: "Every phrase carries the strong case ending on exactly one word — the article when it can show it, otherwise the adjective.",
  },
  {
    title: "Three naked spots",
    body: "ein, kein and the possessives show no ending in masculine nominative and neuter nominative/accusative — there the adjective takes over.",
  },
];

export default function BauteilTrainer({
  round,
  onAttempt,
  onNewRound,
  flow,
  onFlowDone,
  allowGiveUp,
  onGiveUp,
  hideContinue,
}: {
  round: RoundItem[];
  // Judge one typed phrase (POST /bauteil/attempts via the parent, which owns
  // the token and the OBS-007 practice-session id).
  onAttempt: (itemId: string, answer: string) => Promise<BauteilVerdict>;
  // Fetch a fresh round; the parent remounts this component with it.
  onNewRound: () => void;
  // FLOW-001: mixed-practice mode — the parent deals exactly one item via
  // `round` and remounts per turn (via `key`), so this trainer only needs to
  // skip its own intro/round chrome and hand the verdict back instead of
  // ever reaching its own "done" phase.
  flow?: boolean;
  onFlowDone?: (correct: boolean) => void;
  // FLOW-002: the deliberate "give up" escape — Flow-only (default false),
  // so standalone practice stays pixel-identical.
  allowGiveUp?: boolean;
  onGiveUp?: (itemId: string) => Promise<BauteilVerdict>;
  // CLARA-14: Clara's room only — after a graded verdict, suppress the
  // advance button/Enter keybinding and never call onFlowDone; the parent
  // (TeacherChat) dismisses the card externally. Absent/false (Flow, every
  // other caller) is byte-identical.
  hideContinue?: boolean;
}) {
  const [phase, setPhase] = useState<Phase>(flow ? "drill" : "intro");
  const [queue, setQueue] = useState<QueueItem[]>(round);
  const [index, setIndex] = useState(0);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [insufficient, setInsufficient] = useState<{ needed: number; available: number } | null>(null);
  const [verdict, setVerdict] = useState<BauteilVerdict | null>(null);
  const [firstTryGreens, setFirstTryGreens] = useState(0);
  // First-try misses with the solution captured from the verdict, so the
  // round summary can show it (the round payload itself carries no answers).
  const [missed, setMissed] = useState<{ item: RoundItem; expected: string }[]>(
    []
  );
  const inputRef = useRef<HTMLInputElement>(null);

  const item = queue[index];

  useEffect(() => {
    if (phase === "drill" && verdict === null) {
      inputRef.current?.focus();
    }
  }, [phase, index, verdict]);

  // GAME-001: "Round complete" is standalone-only — flow mode hands off via
  // onFlowDone before phase ever reaches "done" (see advance below).
  const doneSoundRef = useRef(false);
  useEffect(() => {
    if (phase === "done" && !doneSoundRef.current) {
      doneSoundRef.current = true;
      playSound("bigwin");
    }
  }, [phase]);

  const advance = useCallback(() => {
    // FLOW-001: no "done" phase in flow mode — hand the verdict to the
    // parent, which deals the next item (a fresh mount, via `key`).
    if (flow) {
      const correct = verdict?.correct ?? false;
      setVerdict(null);
      setValue("");
      onFlowDone?.(correct);
      return;
    }
    setVerdict(null);
    setValue("");
    if (index + 1 >= queue.length) {
      setPhase("done");
    } else {
      setIndex(index + 1);
    }
  }, [index, queue.length, flow, verdict, onFlowDone]);

  // Enter advances from the feedback state (the input is unmounted then).
  useEffect(() => {
    if (verdict === null || hideContinue) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        advance();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [verdict, advance, hideContinue]);

  async function check(e: React.FormEvent) {
    e.preventDefault();
    const answer = value.trim();
    if (!answer || busy) return;
    setBusy(true);
    setFailed(null);
    try {
      const res = await onAttempt(item.id, answer);
      setVerdict(res);
      playSound(res.correct ? "win" : "fail");
      if (res.correct) {
        if (!item.retry) setFirstTryGreens((n) => n + 1);
      } else if (!item.retry) {
        setMissed((m) => [...m, { item, expected: res.expected }]);
        // FLOW-001: no second-chance retry re-queue in flow mode — the queue
        // stays exactly the one dealt item.
        if (!flow) setQueue((q) => [...q, { ...item, retry: true }]);
      }
    } catch (err) {
      if (err instanceof InsufficientCoinsError) {
        setInsufficient({ needed: err.needed, available: err.available });
        refreshCoins();
      } else {
        setFailed(
          err instanceof Error && err.message && !err.message.includes("failed (")
            ? err.message
            : "Couldn't check that — try again in a moment."
        );
      }
    } finally {
      setBusy(false);
    }
  }

  // FLOW-002: the deliberate give-up — grades a real miss server-side and
  // reveals the answer through the same verdict card a wrong `check()`
  // shows. `value` is overwritten so "You typed:" reads honestly instead of
  // echoing whatever partial text was sitting in the box.
  async function giveUp() {
    if (busy || !onGiveUp) return;
    setBusy(true);
    setFailed(null);
    try {
      const res = await onGiveUp(item.id);
      setValue("(gave up)");
      setVerdict(res);
      if (!item.retry) setMissed((m) => [...m, { item, expected: res.expected }]);
    } catch (err) {
      if (err instanceof InsufficientCoinsError) {
        setInsufficient({ needed: err.needed, available: err.available });
        refreshCoins();
      } else {
        setFailed(
          err instanceof Error && err.message && !err.message.includes("failed (")
            ? err.message
            : "Couldn't check that — try again in a moment."
        );
      }
    } finally {
      setBusy(false);
    }
  }

  if (phase === "intro") {
    return (
      <div
        className="rounded-[28px] border-[3px] border-line bg-card p-7"
        style={inkShadow}
      >
        <h2 className="font-display text-[20px] font-black tracking-tight text-ink">
          How it works
        </h2>
        <ul className="mt-5 space-y-4">
          {RULES.map((r) => (
            <li key={r.title}>
              <p className="font-body text-[12px] font-black uppercase tracking-[0.18em] text-flag-red">
                {r.title}
              </p>
              <p className="mt-1 font-body text-[14px] leading-relaxed text-ink-soft">
                {r.body}
              </p>
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={() => setPhase("drill")}
          className="btn-3d mt-7 inline-flex items-center rounded-[20px] border-[3px] border-red-line bg-flag-red-fill px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-on-fill"
          style={redShadow}
        >
          Start · {round.length} Sätze
        </button>
      </div>
    );
  }

  if (phase === "done") {
    return (
      <div
        className="rounded-[28px] border-[3px] border-line bg-card p-7 text-center"
        style={inkShadow}
      >
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          Round complete
        </p>
        <h2 className="mt-2 font-display text-[clamp(28px,5vw,40px)] font-black tracking-tight text-ink">
          {firstTryGreens} / {round.length} first try
        </h2>
        {missed.length > 0 && (
          <div className="mx-auto mt-5 max-w-[420px] text-left">
            <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              Worth another look
            </p>
            <ul className="mt-2 space-y-1.5">
              {missed.map((m) => (
                <li
                  key={m.item.id}
                  className="font-body text-[14px] text-ink-soft"
                >
                  {m.item.parts.join(" · ")} →{" "}
                  <span className="font-bold text-ink">{m.expected}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-7 flex items-center justify-center gap-5">
          <button
            type="button"
            onClick={onNewRound}
            className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-red-line bg-flag-red-fill px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-on-fill"
            style={redShadow}
          >
            New round
          </button>
          <Link
            href="/practice"
            className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
          >
            ← Back to menu
          </Link>
        </div>
      </div>
    );
  }

  const [before, after] = item.frame.split("___");
  const solved = verdict !== null;
  const tint = !solved
    ? "border-line bg-card"
    : verdict.correct
      ? "border-success bg-success-soft"
      : "border-flag-red bg-flag-red-soft";

  return (
    <div>
      {/* FLOW-001: the Flow page shows its own progress — hide this round's. */}
      {!flow && (
        <div className="mb-3 flex items-center justify-between">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {index + 1} / {queue.length}
            {item.retry ? " · second try" : ""}
          </p>
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {firstTryGreens} ✓
          </p>
        </div>
      )}

      <div
        className={`rounded-[28px] border-[3px] p-7 transition-colors ${tint}`}
        style={inkShadow}
      >
        {/* The raw parts — the material to bend. */}
        <div className="flex flex-wrap items-center justify-center gap-2">
          {item.parts.map((p, i) => (
            <span key={i} className="flex items-center gap-2">
              {i > 0 && (
                <span aria-hidden className="text-[18px] font-black text-ink-muted">
                  ·
                </span>
              )}
              <span className="rounded-[14px] border-[3px] border-line bg-card px-3.5 py-1.5 font-display text-[18px] font-black text-ink">
                {p}
              </span>
            </span>
          ))}
        </div>

        {/* The frame — it decides the case. */}
        <p className="mt-6 text-center font-body text-[20px] leading-relaxed text-ink">
          {before}
          {solved ? (
            <span
              className={`font-bold ${verdict.correct ? "text-success" : "text-flag-red-deep"}`}
            >
              {verdict.expected}
            </span>
          ) : (
            <span
              aria-label="gap"
              className="mx-1 inline-block min-w-[110px] border-b-[3px] border-line align-baseline"
            >
              &nbsp;
            </span>
          )}
          {after}
        </p>
        <p className="mt-2 text-center font-body text-[13px] italic text-ink-muted">
          {item.hint}
        </p>

        {!solved ? (
          <form onSubmit={check} className="mt-6">
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                ref={inputRef}
                type="text"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="Type the phrase…"
                lang="de"
                autoCapitalize="off"
                autoComplete="off"
                autoCorrect="off"
                spellCheck={false}
                maxLength={120}
                className="min-w-0 flex-1 rounded-[18px] border-[3px] border-line bg-card px-4 py-3 font-body text-[16px] text-ink outline-none placeholder:text-ink-faint focus:border-flag-red"
              />
              <button
                type="submit"
                disabled={busy || !value.trim()}
                className="btn-3d inline-flex items-center justify-center rounded-[18px] border-[3px] border-red-line bg-flag-red-fill px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-on-fill disabled:pointer-events-none disabled:opacity-40"
                style={redShadow}
              >
                {busy ? "Checking…" : "Check"}
              </button>
            </div>
            {insufficient && (
              <div className="mt-3">
                <OutOfCoinsPanel needed={insufficient.needed} available={insufficient.available} onDismiss={() => setInsufficient(null)} />
              </div>
            )}
            {failed && (
              <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-red-deep">
                {failed}
              </p>
            )}
            {/* FLOW-002: deliberately unstyled/small — never a rival to Check. */}
            {allowGiveUp && onGiveUp && (
              <p className="mt-4 text-center">
                <button
                  type="button"
                  onClick={giveUp}
                  disabled={busy}
                  className="font-body text-[11px] text-ink-muted underline-offset-2 hover:underline disabled:pointer-events-none disabled:opacity-40"
                >
                  Give up
                </button>
              </p>
            )}
          </form>
        ) : (
          <div className="mt-6">
            {!verdict.correct && (
              <FeedbackCard
                attempt={value.trim()}
                corrected={verdict.expected}
                note={verdict.note}
              >
                {/* The two axes, never conflated (GRAM-002 rule 6): did they
                    aim at the right case, and did the endings execute it. */}
                <div className="mt-1 flex gap-2.5">
                  <AxisPill label="Case" ok={verdict.caseOk} />
                  <AxisPill label="Endings" ok={verdict.carrierOk} />
                </div>
              </FeedbackCard>
            )}
            {/* CLARA-14: Clara's room hides this — the card dismisses when
                her spoken reaction lands (or a 15s backstop), not on a
                learner click. See hideContinue's doc comment above. */}
            {!hideContinue && (
              <div className="mt-5 flex items-center justify-center gap-4">
                <button
                  type="button"
                  onClick={advance}
                  className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-line bg-card px-6 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
                  style={inkShadow}
                >
                  {flow ? "Next" : index + 1 >= queue.length ? "Finish" : "Next"}
                </button>
                <span className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                  or press Enter
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function AxisPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border-[2px] px-3 py-1 font-body text-[11px] font-black uppercase tracking-[0.14em] ${
        ok
          ? "border-success bg-success-soft text-success"
          : "border-flag-red bg-card text-flag-red-deep"
      }`}
    >
      {ok ? "✓" : "✕"} {label}
    </span>
  );
}
