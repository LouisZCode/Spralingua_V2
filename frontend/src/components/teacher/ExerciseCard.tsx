"use client";

import { useEffect, useRef, useState } from "react";
import { FeedbackCard } from "../shared/feedback";

// AGENT-00X: the generic practice-item card Clara's exercise loop renders.
// teacher/routes.py normalizes whichever of the five underlying drills an
// item came from into one shape ({instruction, prompt}), so this card
// doesn't know or care which drill it is — it just asks the question and
// renders the verdict TeacherChat hands back.
//
// Deliberately not a modal: no backdrop, no focus trap, no Escape handler.
// TeacherChat floats this over the live chat (see that file) so the
// learner can keep talking to Clara and simply never look at it.

export type ExerciseData = {
  drill: string;
  itemId: string;
  patternId: string;
  instruction: string;
  prompt: string;
};

export type ExerciseVerdict = {
  correct: boolean;
  expected: string;
  note: string | null;
};

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;

export default function ExerciseCard({
  data,
  onSubmit,
  onSkip,
}: {
  data: ExerciseData;
  // Owned by the parent: POSTs /teacher/exercise/attempts AND fires the
  // ÜBUNGSERGEBNIS report (see TeacherChat.tsx::handleAnswer). This card
  // only renders whatever verdict comes back, or an inline retry prompt if
  // the promise rejects.
  onSubmit: (answer: string) => Promise<ExerciseVerdict>;
  onSkip: () => void;
}) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<ExerciseVerdict | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function check() {
    const trimmed = answer.trim();
    if (!trimmed || busy || verdict) return;
    setBusy(true);
    setError(null);
    try {
      const v = await onSubmit(trimmed);
      setVerdict(v);
    } catch (e) {
      setError(
        e instanceof Error && e.message
          ? e.message
          : "Couldn't check that — try again."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-label="Practice exercise"
      className="pointer-events-auto w-full max-w-[400px] rounded-[24px] border-[3px] border-ink bg-paper-warm p-5 shadow-[0_8px_0_var(--color-ink)]"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="font-body text-[10px] font-black uppercase tracking-[0.28em] text-ink-muted">
          Quick practice
        </span>
        {/* Unobtrusive — never a rival to Check, and gone once graded (the
            card is answered at that point, there's nothing left to skip). */}
        {!verdict && (
          <button
            type="button"
            onClick={onSkip}
            disabled={busy}
            className="font-body text-[10px] font-bold uppercase tracking-[0.2em] text-ink-faint underline-offset-2 hover:text-ink-muted hover:underline disabled:pointer-events-none disabled:opacity-40"
          >
            Skip
          </button>
        )}
      </div>

      <p className="mt-2 font-body text-[13px] leading-snug text-ink-soft">
        {data.instruction}
      </p>
      <p className="mt-3 whitespace-pre-wrap font-display text-[17px] font-bold leading-snug text-ink">
        {data.prompt}
      </p>

      {!verdict ? (
        <div className="mt-4">
          <div className="flex flex-col gap-2.5 sm:flex-row">
            <input
              ref={inputRef}
              type="text"
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  check();
                }
              }}
              disabled={busy}
              placeholder="Type your answer…"
              lang="de"
              autoCapitalize="off"
              autoComplete="off"
              autoCorrect="off"
              spellCheck={false}
              maxLength={200}
              className="min-w-0 flex-1 rounded-[16px] border-[3px] border-ink bg-white px-3.5 py-2.5 font-body text-[15px] text-ink outline-none placeholder:text-ink-faint focus:border-flag-red disabled:opacity-60"
            />
            <button
              type="button"
              onClick={check}
              disabled={busy || !answer.trim()}
              className="btn-3d inline-flex items-center justify-center gap-2 rounded-[16px] border-[3px] border-flag-red-deep bg-flag-red px-5 py-2.5 font-display text-[12px] font-black uppercase tracking-[0.16em] text-white disabled:pointer-events-none disabled:opacity-40"
              style={redShadow}
            >
              {busy && (
                <span
                  aria-hidden
                  className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white/50 border-t-white"
                />
              )}
              {busy ? "Checking…" : "Check"}
            </button>
          </div>
          {error && (
            <p className="mt-2 font-body text-[12px] font-semibold text-flag-red-deep">
              {error}
            </p>
          )}
        </div>
      ) : verdict.correct ? (
        <div className="mt-4 rounded-[16px] border-[3px] border-success bg-success-soft px-4 py-3 text-center">
          <p className="font-display text-[15px] font-black text-success">
            ✓ Correct
          </p>
          {verdict.note && (
            <p className="mt-1 font-body text-[12px] leading-snug text-ink-soft">
              {verdict.note}
            </p>
          )}
        </div>
      ) : (
        // HOUSE RULE: never strike through the learner's answer — FeedbackCard
        // marks the wrong tokens red and the correction green, nothing more.
        <div className="mt-4">
          <FeedbackCard
            attempt={answer.trim()}
            corrected={verdict.expected}
            note={verdict.note}
          />
        </div>
      )}
    </div>
  );
}
