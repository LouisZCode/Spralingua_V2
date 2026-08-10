"use client";

import type { ReactNode } from "react";

// ─── UI-008: one feedback skeleton for every drill ──────────────────────────
// Word-level diff between attempt and correction so the error (red, in the
// attempt) and the fix (green, in the correction) register at a glance.
// Tokens compare case-sensitively by default — capitalization errors are real
// errors in German the learner TYPED — after stripping surrounding
// punctuation, so a trailing period never lights up a word. SPOKEN surfaces
// (VocabTrainer, tandem debrief, Development slips) pass `caseInsensitive`:
// there the "attempt" is an STT transcript whose casing is an ASR artifact,
// and a judge that lowercases its correction must not turn every capitalized
// noun red (SATZ-016). Never strikethrough: red alone marks the error.

export type MarkedToken = { text: string; changed: boolean };

const strip = (t: string) =>
  t.replace(/^[„“”"'‚‘.,!?;:()[\]]+|[„“”"'‚‘.,!?;:()[\]]+$/g, "");

export function diffTokens(
  attempt: string,
  corrected: string,
  opts?: { caseInsensitive?: boolean },
): { attempt: MarkedToken[]; corrected: MarkedToken[] } {
  const norm = opts?.caseInsensitive
    ? (t: string) => strip(t).toLowerCase()
    : strip;
  const a = attempt.split(/\s+/).filter(Boolean);
  const b = corrected.split(/\s+/).filter(Boolean);
  const an = a.map(norm);
  const bn = b.map(norm);
  const dp: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] =
        an[i] === bn[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const aOut = a.map((text) => ({ text, changed: true }));
  const bOut = b.map((text) => ({ text, changed: true }));
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (an[i] === bn[j]) {
      aOut[i].changed = false;
      bOut[j].changed = false;
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i++;
    } else {
      j++;
    }
  }
  return { attempt: aOut, corrected: bOut };
}

export function MarkedText({
  tokens,
  mark,
  renderToken,
}: {
  tokens: MarkedToken[];
  // "blue" is IDIOM-002's mark: what the Germanizer changed. Not red —
  // the learner's line wasn't WRONG, a native just phrases it differently.
  mark: "red" | "green" | "blue";
  // SATZ-018: optional per-token content override — lets a caller (e.g. the
  // corrected sentence on a Satzschmiede vocab card) wrap each token in
  // something else (Glossable) while this component still owns the diff
  // color + spacing, so the two never fight over tokenization. Omitted by
  // every other caller today — identical rendering to before.
  renderToken?: (token: MarkedToken, index: number) => ReactNode;
}) {
  const markClass =
    mark === "red"
      ? "font-semibold text-flag-red-deep"
      : mark === "blue"
        ? "font-semibold text-blue-600"
        : "text-success";
  return (
    <>
      {tokens.map((t, i) => (
        <span key={i} className={t.changed ? markClass : undefined}>
          {renderToken ? renderToken(t, i) : t.text}
          {i < tokens.length - 1 ? " " : ""}
        </span>
      ))}
    </>
  );
}

// The shared wrong-answer block: attempt (red-marked) → correction
// (green-marked) → per-drill extras (children) → explanation note.
export function FeedbackCard({
  attempt,
  corrected,
  note,
  accent = "red",
  children,
}: {
  attempt: string;
  corrected?: string | null;
  note?: string | null;
  accent?: "red" | "gold";
  children?: ReactNode;
}) {
  const d = corrected ? diffTokens(attempt, corrected) : null;
  return (
    <div className="flex flex-col items-center gap-2">
      <p className="max-w-[460px] text-center font-body text-[13px] text-ink-soft">
        You typed:{" "}
        {d ? (
          <MarkedText tokens={d.attempt} mark="red" />
        ) : (
          <span className="font-semibold">{attempt}</span>
        )}
      </p>
      {d && (
        <p className="max-w-[460px] text-center font-body text-[18px] font-bold leading-snug text-ink">
          <span
            className={`mr-1.5 ${
              accent === "gold" ? "text-flag-gold-deep" : "text-flag-red-deep"
            }`}
          >
            →
          </span>
          <MarkedText tokens={d.corrected} mark="green" />
        </p>
      )}
      {children}
      {note && (
        <p className="mt-1 max-w-[420px] text-center font-body text-[13px] leading-snug text-ink-soft">
          {note}
        </p>
      )}
    </div>
  );
}
