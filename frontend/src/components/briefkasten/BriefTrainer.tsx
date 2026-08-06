"use client";

import { useState } from "react";
import Link from "next/link";
import type {
  AttemptResult,
  Explanation,
  FeedbackResult,
  HintItem,
  HintResult,
  Letter,
} from "./api";
import { diffTokens, MarkedText, type MarkedToken } from "../shared/feedback";

type Phase = "writing" | "hints" | "feedback";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

const CATEGORY_LABEL: Record<HintItem["category"], string> = {
  grammatik: "Grammar",
  wortstellung: "Word order",
  wortschatz: "Vocabulary",
  rechtschreibung: "Spelling",
};
const CATEGORY_ORDER = Object.keys(CATEGORY_LABEL) as HintItem["category"][];

function wordCount(text: string): number {
  const t = text.trim();
  return t ? t.split(/\s+/).length : 0;
}

// The four required points as a checklist. Plain (no ticks) until a hint
// verdict comes back with `coveredPoints` — the writing phase has no
// coverage signal yet, so it deliberately shows an undecided state rather
// than guessing.
function PointsChecklist({
  points,
  covered,
}: {
  points: string[];
  covered?: boolean[];
}) {
  return (
    <ul className="space-y-2">
      {points.map((p, i) => {
        const isCovered = covered?.[i];
        const known = covered !== undefined;
        return (
          <li
            key={i}
            className="flex items-start gap-2.5 font-body text-[13px] leading-snug"
          >
            <span
              aria-hidden
              className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border-[2px] font-black text-[11px] ${
                !known
                  ? "border-ink/25 text-transparent"
                  : isCovered
                    ? "border-success bg-success text-white"
                    : "border-flag-red bg-white text-flag-red"
              }`}
            >
              {known ? (isCovered ? "✓" : "!") : "·"}
            </span>
            <span className={known && !isCovered ? "text-ink" : "text-ink-soft"}>
              {p}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// The shared diff engine tokenizes on /\s+/, which is right for the one-line
// answers every other drill marks up and wrong for a letter: it would collapse
// greeting, paragraphs and closing into a single blob, and the shape of a
// letter is part of what's being learned here. So we keep `diffTokens` (one
// diff engine in the codebase, not two) and re-drape its tokens back over the
// original line structure — walking the lines in order and consuming as many
// tokens as each line had words. Blank lines survive as spacing.
function MarkedLetter({
  text,
  tokens,
  mark,
}: {
  text: string;
  tokens: MarkedToken[];
  mark: "red" | "green";
}) {
  const counts = text
    .split("\n")
    .map((line) => line.split(/\s+/).filter(Boolean).length);
  // Prefix sums rather than a running cursor: same walk, no mutation during
  // render (react-hooks/immutability).
  const lines = counts.map((count, i) => {
    const start = counts.slice(0, i).reduce((sum, n) => sum + n, 0);
    return tokens.slice(start, start + count);
  });
  return (
    <div className="space-y-1">
      {lines.map((lineTokens, i) =>
        lineTokens.length === 0 ? (
          <div key={i} className="h-2" />
        ) : (
          <p key={i} className="font-body text-[14px] leading-relaxed">
            <MarkedText tokens={lineTokens} mark={mark} />
          </p>
        )
      )}
    </div>
  );
}

function LetterPanel({ letter }: { letter: Letter }) {
  return (
    <div
      className="rounded-[28px] border-[3px] border-ink bg-white p-7"
      style={inkShadow}
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          From {letter.sender.name} · {letter.sender.relation}
        </p>
        <span className="inline-flex items-center rounded-full border-[2px] border-ink bg-white px-3 py-0.5 font-body text-[10px] font-black uppercase tracking-[0.16em] text-ink-muted">
          {letter.level.toUpperCase()} · {letter.register}
        </span>
      </div>
      <p className="mt-4 font-display text-[17px] font-black leading-snug text-ink">
        {letter.betreff}
      </p>
      <p className="mt-4 whitespace-pre-line font-body text-[15px] leading-relaxed text-ink">
        {letter.body}
      </p>
    </div>
  );
}

function Footer({ onNewLetter }: { onNewLetter: () => void }) {
  return (
    <div className="mt-8 flex items-center justify-center gap-5">
      <button
        type="button"
        onClick={onNewLetter}
        className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-white"
        style={redShadow}
      >
        New letter
      </button>
      <Link
        href="/practice"
        className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
      >
        ← Back to practice
      </Link>
    </div>
  );
}

export default function BriefTrainer({
  letter,
  onAttempt,
  onNewLetter,
}: {
  letter: Letter;
  // Judge one attempt (POST /briefkasten/attempts via the parent, which owns
  // the token and the OBS-007 practice-session id). `attempt` discriminates
  // the union it resolves with — see api.ts.
  onAttempt: (params: {
    seedId: string;
    letterBody: string;
    points: string[];
    response: string;
    firstAttempt?: string;
    attempt: 1 | 2;
  }) => Promise<AttemptResult>;
  // Fetch a fresh letter; the parent remounts this component with it.
  onNewLetter: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("writing");
  // The learner's reply — survives the attempt-1 -> attempt-2 transition on
  // purpose: revising the same letter IS the exercise, retyping it is a bug.
  const [text, setText] = useState("");
  const [firstAttemptText, setFirstAttemptText] = useState<string | null>(null);
  const [hintResult, setHintResult] = useState<HintResult | null>(null);
  const [feedbackResult, setFeedbackResult] = useState<FeedbackResult | null>(
    null
  );
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [showNatural, setShowNatural] = useState(false);

  const words = wordCount(text);
  const inRange = words >= letter.wordTarget.min && words <= letter.wordTarget.max;
  const canSubmit = text.trim().length > 0 && !submitting;

  async function submitFirst() {
    setSubmitting(true);
    setFailed(null);
    try {
      const result = await onAttempt({
        seedId: letter.seedId,
        letterBody: letter.body,
        points: letter.points,
        response: text,
        attempt: 1,
      });
      if (result.attempt === 1) {
        setFirstAttemptText(text);
        setHintResult(result);
        setPhase("hints");
      }
    } catch (err) {
      setFailed(
        err instanceof Error && err.message && !err.message.includes("failed (")
          ? err.message
          : "Couldn't check that — try again in a moment."
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function submitSecond() {
    setSubmitting(true);
    setFailed(null);
    try {
      const result = await onAttempt({
        seedId: letter.seedId,
        letterBody: letter.body,
        points: letter.points,
        response: text,
        firstAttempt: firstAttemptText ?? text,
        attempt: 2,
      });
      if (result.attempt === 2) {
        setFeedbackResult(result);
        setPhase("feedback");
      }
    } catch (err) {
      setFailed(
        err instanceof Error && err.message && !err.message.includes("failed (")
          ? err.message
          : "Couldn't check that — try again in a moment."
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (phase === "feedback" && feedbackResult) {
    const diff = diffTokens(text, feedbackResult.correctedText);
    return (
      <div>
        <div
          className="mx-auto max-w-[560px] rounded-[28px] border-[3px] border-ink bg-white p-7"
          style={inkShadow}
        >
          <div className="text-center">
            <span className="inline-flex items-center rounded-full border-[2px] border-ink bg-ink px-4 py-1.5 font-display text-[13px] font-black uppercase tracking-[0.14em] text-white">
              {feedbackResult.score}/100
            </span>
            <p className="mt-3 font-display text-[18px] font-black leading-snug text-ink">
              {feedbackResult.feedback}
            </p>
            {feedbackResult.improvementsFromFirst && (
              <p className="mt-2 font-body text-[13px] leading-snug text-ink-soft">
                {feedbackResult.improvementsFromFirst}
              </p>
            )}
          </div>

          <div className="mt-6 space-y-4">
            <div className="rounded-[18px] border-[3px] border-ink bg-white px-4 py-3">
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                What you wrote
              </p>
              <div className="mt-2 text-ink-soft">
                <MarkedLetter text={text} tokens={diff.attempt} mark="red" />
              </div>
            </div>
            <div className="rounded-[18px] border-[3px] border-ink bg-paper-warm px-4 py-3">
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                Corrected
              </p>
              <div className="mt-2 text-ink">
                <MarkedLetter
                  text={feedbackResult.correctedText}
                  tokens={diff.corrected}
                  mark="green"
                />
              </div>
            </div>
          </div>

          {feedbackResult.explanations.length > 0 && (
            <div className="mt-6">
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                What to fix
              </p>
              <ul className="mt-2 space-y-2.5">
                {feedbackResult.explanations.map((e: Explanation, i: number) => (
                  <li
                    key={i}
                    className="rounded-[16px] border-[3px] border-ink bg-white px-4 py-3"
                  >
                    <p className="font-body text-[13px] font-semibold text-flag-red-deep">
                      {e.error}
                    </p>
                    <p className="mt-1 font-body text-[15px] font-bold text-success">
                      → {e.correction}
                    </p>
                    <p className="mt-1 font-body text-[12px] leading-snug text-ink-muted">
                      {e.why}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {feedbackResult.focusPoints.length > 0 && (
            <div className="mt-6 rounded-[18px] border-[3px] border-ink bg-white px-4 py-4">
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                Focus for next time
              </p>
              <ul className="mt-2 list-disc space-y-1 pl-5">
                {feedbackResult.focusPoints.map((p, i) => (
                  <li key={i} className="font-body text-[14px] text-ink-soft">
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* IDIOM-002: renders nothing at all when null — no empty card,
              no placeholder. Present only when a native would genuinely
              phrase things differently. */}
          {feedbackResult.naturalVersion && (
            <div className="mt-6 text-center">
              <button
                type="button"
                onClick={() => setShowNatural((v) => !v)}
                className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-white px-5 py-2 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink"
                style={inkShadow}
              >
                {showNatural
                  ? "Hide ▴"
                  : "How would a German write this? ▾"}
              </button>
              {showNatural && (
                <div className="mt-3 rounded-[18px] border-[3px] border-ink bg-paper-warm px-4 py-3 text-left">
                  <p className="whitespace-pre-line font-body text-[15px] leading-relaxed text-ink">
                    {feedbackResult.naturalVersion}
                  </p>
                </div>
              )}
            </div>
          )}
        </div>

        <Footer onNewLetter={onNewLetter} />
      </div>
    );
  }

  // phase === "writing" | "hints"
  return (
    <div>
      <div className="grid gap-6 lg:grid-cols-2">
        <LetterPanel letter={letter} />

        <div
          className="rounded-[28px] border-[3px] border-ink bg-white p-7"
          style={inkShadow}
        >
          {phase === "hints" && hintResult && (
            <>
              <p className="font-display text-[15px] font-black leading-snug text-ink">
                {hintResult.message}
              </p>
              {hintResult.items.length > 0 && (
                <div className="mt-5 space-y-4">
                  {CATEGORY_ORDER.map((cat) => {
                    const items = hintResult.items.filter(
                      (i) => i.category === cat
                    );
                    if (items.length === 0) return null;
                    return (
                      <div
                        key={cat}
                        className="rounded-[16px] border-[3px] border-ink bg-white px-4 py-3"
                      >
                        <p className="font-body text-[10px] font-black uppercase tracking-[0.2em] text-flag-red">
                          {CATEGORY_LABEL[cat]}
                        </p>
                        <ul className="mt-2 space-y-3">
                          {items.map((item, i) => (
                            <li key={i}>
                              <p className="font-body text-[14px] italic leading-snug text-ink">
                                “{item.text}”
                              </p>
                              <p className="mt-1 font-body text-[13px] leading-snug text-ink-soft">
                                {item.hint}
                              </p>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="mt-6 border-t-[3px] border-dashed border-ink/15 pt-5">
                <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                  What to cover
                </p>
                <div className="mt-2">
                  <PointsChecklist
                    points={letter.points}
                    covered={hintResult.coveredPoints}
                  />
                </div>
              </div>
            </>
          )}

          {phase === "writing" && (
            <div>
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                What to cover
              </p>
              <div className="mt-2">
                <PointsChecklist points={letter.points} />
              </div>
            </div>
          )}

          <div className="mt-5">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={11}
              placeholder="Schreib deine Antwort auf Deutsch…"
              className="w-full resize-y rounded-[18px] border-[3px] border-ink bg-white p-4 font-body text-[15px] leading-relaxed text-ink outline-none focus:border-flag-red-deep"
            />
            <p
              className={`mt-2 font-body text-[11px] font-bold uppercase tracking-[0.16em] ${
                inRange ? "text-success" : "text-ink-muted"
              }`}
            >
              {words} {words === 1 ? "word" : "words"} · aim for{" "}
              {letter.wordTarget.min}–{letter.wordTarget.max}
            </p>
          </div>

          {failed && (
            <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-red-deep">
              {failed}
            </p>
          )}

          <div className="mt-5 flex justify-center">
            {phase === "writing" ? (
              <button
                type="button"
                disabled={!canSubmit}
                onClick={submitFirst}
                className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-white disabled:cursor-not-allowed disabled:opacity-40"
                style={redShadow}
              >
                {submitting ? "Sending…" : "Send letter"}
              </button>
            ) : (
              <button
                type="button"
                disabled={!canSubmit}
                onClick={submitSecond}
                className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-white disabled:cursor-not-allowed disabled:opacity-40"
                style={redShadow}
              >
                {submitting ? "Sending…" : "Send the improved version"}
              </button>
            )}
          </div>
        </div>
      </div>

      <Footer onNewLetter={onNewLetter} />
    </div>
  );
}
