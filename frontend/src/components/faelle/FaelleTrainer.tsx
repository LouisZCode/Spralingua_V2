"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { CaseItem, CaseVerdict } from "./api";
import Glossable from "../shared/Glossable";
import type { GlossInfo } from "../satzschmiede/api";
import { FeedbackCard } from "../shared/feedback";
import { playSound } from "../shared/sound";
import { InsufficientCoinsError } from "@/lib/coins";
import { OutOfCoinsPanel, refreshCoins } from "../shared/Coins";

// A missed item returns once at the end of the round — same second-chance
// contract as VerbindungenTrainer. `retry` marks the copy.
type QueueItem = CaseItem & { retry?: boolean };

type Phase = "intro" | "drill" | "done";

const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// The drill rules, shown once up front — same convention as
// VerbindungenTrainer. The last bullet previews the exercise's whole
// teaching payload: a wrong case is very often still real German, just
// German that says something else.
const RULES: { title: string; body: string }[] = [
  {
    title: "Complete the case",
    body: "German case marking lives in small words — an article, a pronoun, a preposition's ending. Type exactly what the gap is missing.",
  },
  {
    title: "Two-way prepositions ask \"wohin oder wo?\"",
    body: "in / an / auf / über / unter switch between two cases with the SAME preposition — movement takes one, location takes the other. The gap never says which; the sentence does.",
  },
  {
    title: "Some prepositions and verbs never switch",
    body: "mit / nach / bei / von / zu / aus / seit always take one case. helfen / danken / gefallen and a few other verbs always take another. Learn the group, not the sentence.",
  },
  {
    title: "A wrong case is often just a different sentence",
    body: "„auf dem Tisch“ instead of „auf den Tisch“ isn't broken — it says the vase was already there. When that happens, we'll tell you what you actually said.",
  },
];

export default function FaelleTrainer({
  round,
  onAttempt,
  onNewRound,
  onGloss,
  onAdd,
  flow,
  onFlowDone,
  allowGiveUp,
  onGiveUp,
  hideContinue,
}: {
  round: CaseItem[];
  // Judge one typed gap-fill (POST /faelle/attempts via the parent, which
  // owns the token and the OBS-007 practice-session id).
  onAttempt: (itemId: string, answer: string) => Promise<CaseVerdict>;
  // Fetch a fresh round; the parent remounts this component with it.
  onNewRound: () => void;
  // UI-007: word-gloss popover wiring — optional, absent renders plain text.
  onGloss?: (word: string, context: string) => Promise<GlossInfo>;
  // SATZ-013: resolves with the remaining gloss-path adds today (0..3) so
  // the popover can update its own counter after a successful add.
  onAdd?: (lemma: string) => Promise<{ glossRemaining?: number } | void>;
  // FLOW-001: mixed-practice mode — the parent deals exactly one item via
  // `round` and remounts per turn (via `key`), so this trainer only needs to
  // skip its own intro/round chrome and hand the verdict back instead of
  // ever reaching its own "done" phase.
  flow?: boolean;
  onFlowDone?: (correct: boolean) => void;
  // FLOW-002: the deliberate "give up" escape — Flow-only (default false),
  // so standalone practice stays pixel-identical.
  allowGiveUp?: boolean;
  onGiveUp?: (itemId: string) => Promise<CaseVerdict>;
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
  const [verdict, setVerdict] = useState<CaseVerdict | null>(null);
  // The English hint primes L1 interference, so it stays hidden until
  // asked; grading always reveals it.
  const [hintShown, setHintShown] = useState(false);
  const [firstTryGreens, setFirstTryGreens] = useState(0);
  // First-try misses with the rule captured from the verdict — the round
  // summary shows the rules to memorize (that's the take-away of the drill).
  const [missed, setMissed] = useState<{ item: CaseItem; rule: string }[]>([]);
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
    setHintShown(false);
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
        setMissed((m) => [...m, { item, rule: res.rule }]);
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
  // reveals the rule through the same verdict card a wrong `check()` shows.
  // `value` is overwritten so "You typed:" reads honestly instead of echoing
  // whatever partial text was sitting in the box.
  async function giveUp() {
    if (busy || !onGiveUp) return;
    setBusy(true);
    setFailed(null);
    try {
      const res = await onGiveUp(item.id);
      setValue("(gave up)");
      setVerdict(res);
      if (!item.retry) setMissed((m) => [...m, { item, rule: res.rule }]);
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
          <div className="mx-auto mt-5 max-w-[440px] text-left">
            <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              Rules to memorize
            </p>
            <ul className="mt-2 space-y-1.5">
              {missed.map((m) => (
                <li
                  key={m.item.id}
                  className="font-body text-[13px] font-semibold leading-snug text-ink"
                >
                  {m.rule}
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
        {/* The frame — which case, and which form does it need? The gap
            doesn't say. */}
        <p className="text-center font-body text-[20px] leading-relaxed text-ink">
          {onGloss ? (
            <Glossable text={before} onGloss={onGloss} onAdd={onAdd} />
          ) : (
            before
          )}
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
          {onGloss ? (
            <Glossable text={after} onGloss={onGloss} onAdd={onAdd} />
          ) : (
            after
          )}
        </p>
        {solved || hintShown ? (
          <p className="mt-2 text-center font-body text-[13px] italic text-ink-muted">
            {item.hint}
          </p>
        ) : (
          <p className="mt-2 text-center">
            <button
              type="button"
              onClick={() => setHintShown(true)}
              className="font-body text-[11px] font-bold uppercase tracking-[0.2em] text-ink-faint underline-offset-2 hover:text-ink-muted hover:underline"
            >
              Show English
            </button>
          </p>
        )}

        {!solved ? (
          <form onSubmit={check} className="mt-6">
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                ref={inputRef}
                type="text"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="Type the missing words…"
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
            <div className="flex flex-col items-center gap-2">
              {!verdict.correct && (
                <FeedbackCard
                  attempt={value.trim()}
                  corrected={verdict.expected}
                  note={verdict.note}
                />
              )}
              {/* The highest-value line in the whole exercise: the typed
                  answer wasn't wrong, it was a DIFFERENT sentence. Framed in
                  gold — informational, never an error color — and clearly
                  labeled so it never reads as a second correction. */}
              {verdict.meansInstead && (
                <div className="mx-auto mt-1 max-w-[440px] rounded-[18px] border-[3px] border-flag-gold-deep bg-flag-gold-soft p-4 text-left">
                  <p className="font-body text-[11px] font-black uppercase tracking-[0.2em] text-flag-gold-deep">
                    What you actually said
                  </p>
                  <p className="mt-1.5 font-body text-[13px] leading-snug text-ink">
                    {verdict.meansInstead}
                  </p>
                </div>
              )}
              {/* The rule — the actual thing to memorize, shown on green
                  AND red (the drill exists to burn these in). */}
              <p className="mt-1 max-w-[440px] text-center font-body text-[13px] font-black leading-snug tracking-wide text-ink">
                {verdict.rule}
              </p>
            </div>
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
