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
import Glossable from "../shared/Glossable";
import type { GlossInfo } from "../satzschmiede/api";

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

// ─── Task 3: locate each hint's quoted phrase inside the learner's draft ───
// The judge is told to quote "exactly as they wrote it", but LLM quoting
// drifts — trailing punctuation, a collapsed double space, different
// capitalization. We try three passes, most-exact first, and if none land
// we skip the phrase silently: a missed highlight is invisible, a wrong one
// (or a crash) is not acceptable in a learner's own writing.
type DraftMatch = { start: number; end: number };

// Collapses runs of whitespace to a single space and records, for every
// character of the *collapsed* string, which original-string range it
// stands for. That lets a whitespace-insensitive match on the collapsed
// string be translated back into exact offsets in the untouched original —
// required because we still have to reproduce `firstAttemptText` character
// for character, line breaks included.
function collapseWithMap(s: string): {
  collapsed: string;
  starts: number[];
  ends: number[];
} {
  let collapsed = "";
  const starts: number[] = [];
  const ends: number[] = [];
  let i = 0;
  while (i < s.length) {
    if (/\s/.test(s[i])) {
      const runStart = i;
      while (i < s.length && /\s/.test(s[i])) i++;
      collapsed += " ";
      starts.push(runStart);
      ends.push(i);
    } else {
      collapsed += s[i];
      starts.push(i);
      ends.push(i + 1);
      i++;
    }
  }
  return { collapsed, starts, ends };
}

function findQuotedPhrase(haystack: string, needle: string): DraftMatch | null {
  if (!needle.trim()) return null;

  // Pass 1: exact substring.
  const exact = haystack.indexOf(needle);
  if (exact !== -1) return { start: exact, end: exact + needle.length };

  // Pass 2: whitespace-normalized (collapsed runs of whitespace, both
  // sides), mapped back to original offsets.
  const { collapsed, starts, ends } = collapseWithMap(haystack);
  const needleCollapsed = collapseWithMap(needle).collapsed.trim();
  if (needleCollapsed) {
    const idx = collapsed.indexOf(needleCollapsed);
    if (idx !== -1) {
      const lastIdx = idx + needleCollapsed.length - 1;
      return { start: starts[idx], end: ends[lastIdx] };
    }
  }

  // Pass 3: case-insensitive, on the original (un-collapsed) text. German
  // lowercase-folding doesn't change string length for anything this text
  // realistically contains, so original indices stay valid.
  const idxCi = haystack.toLowerCase().indexOf(needle.toLowerCase());
  if (idxCi !== -1) return { start: idxCi, end: idxCi + needle.length };

  return null;
}

// Resolves every hint's quoted phrase to a span, drops any span that
// overlaps one already accepted (earliest-start wins), and renders the
// draft as alternating plain-text / <mark> chunks that together reproduce
// `text` exactly — no character added, dropped, or duplicated.
function renderHighlightedDraft(text: string, items: HintItem[]) {
  const candidates = items
    .map((item) => findQuotedPhrase(text, item.text))
    .filter((m): m is DraftMatch => m !== null)
    .sort((a, b) => a.start - b.start);

  const accepted: DraftMatch[] = [];
  for (const m of candidates) {
    const prev = accepted[accepted.length - 1];
    if (prev && m.start < prev.end) continue; // overlaps — discard
    accepted.push(m);
  }

  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  accepted.forEach((m, i) => {
    if (m.start > cursor) nodes.push(text.slice(cursor, m.start));
    nodes.push(
      <mark
        key={i}
        className="rounded-[4px] bg-flag-gold-soft px-0.5 text-ink"
      >
        {text.slice(m.start, m.end)}
      </mark>
    );
    cursor = m.end;
  });
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
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
  renderToken,
}: {
  text: string;
  tokens: MarkedToken[];
  mark: "red" | "green" | "blue";
  // BRIEF-001: threaded straight through to each line's MarkedText, same
  // per-token override GermanWay/VocabTrainer use for Glossable — MarkedLetter
  // only re-drapes tokens over line structure, it doesn't own rendering.
  renderToken?: (token: MarkedToken, index: number) => React.ReactNode;
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
            <MarkedText tokens={lineTokens} mark={mark} renderToken={renderToken} />
          </p>
        )
      )}
    </div>
  );
}

function LetterPanel({
  letter,
  onGloss,
  onAdd,
}: {
  letter: Letter;
  // UI-007: hover/tap gloss popover for the incoming letter — optional so
  // the panel still renders plain text when the parent hasn't wired a
  // token yet (mirrors every other Glossable call site in the codebase).
  onGloss?: (word: string, context: string) => Promise<GlossInfo>;
  onAdd?: (lemma: string) => Promise<{ glossRemaining?: number } | void>;
}) {
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
        {onGloss ? (
          <Glossable text={letter.betreff} onGloss={onGloss} onAdd={onAdd} />
        ) : (
          letter.betreff
        )}
      </p>
      {/* whitespace-pre-line lives on this wrapping <p> — `white-space` is
          an inherited CSS property, so the paragraph breaks survive even
          though the actual text now sits inside Glossable's inner <span>. */}
      <p className="mt-4 whitespace-pre-line font-body text-[15px] leading-relaxed text-ink">
        {onGloss ? (
          <Glossable text={letter.body} onGloss={onGloss} onAdd={onAdd} />
        ) : (
          letter.body
        )}
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
  onGloss,
  onAdd,
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
  // UI-007: word-gloss popover for the incoming letter (Task 1) — optional,
  // threaded straight down to LetterPanel. Absent means plain text, same
  // as before this wiring landed.
  onGloss?: (word: string, context: string) => Promise<GlossInfo>;
  onAdd?: (lemma: string) => Promise<{ glossRemaining?: number } | void>;
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
    // BRIEF-001: `markPunctuation` on — the learner typed every character of
    // this letter, so a missing/added comma is a real correction, not an ASR
    // artifact to hide (unlike every spoken-surface caller of diffTokens).
    // Case stays sensitive as it was: capitalization is real German grammar
    // the learner typed, not a transcript's guess.
    const diff = diffTokens(text, feedbackResult.correctedText, {
      markPunctuation: true,
    });
    // Nothing changed AND nothing to explain — the "Corrected" box would be
    // a pixel-identical twin of "What you wrote". Show a one-line
    // confirmation in its place instead of two boxes with the same text.
    const nothingToCorrect =
      feedbackResult.explanations.length === 0 &&
      !diff.attempt.some((t) => t.changed) &&
      !diff.corrected.some((t) => t.changed);

    // SATZ-018/BRIEF-001: same per-token Glossable override GermanWay and
    // VocabTrainer use — MarkedText/MarkedLetter still own the diff color
    // and line layout, Glossable just replaces the plain-text leaf. Context
    // sent to onGloss is always the full block being read, not the single
    // tapped word. `undefined` when the parent hasn't wired onGloss, so both
    // boxes fall back to their original plain-text rendering.
    const renderCorrectedToken = onGloss
      ? (t: MarkedToken) => (
          <Glossable
            text={t.text}
            onGloss={(word: string) => onGloss(word, feedbackResult.correctedText)}
            onAdd={onAdd}
          />
        )
      : undefined;

    // IDIOM-002: diffed against the CORRECTED letter (not the learner's raw
    // attempt) — this reveal answers "how would a German phrase the fixed
    // version", so the comparison base is the fix, not the mistake. Blue,
    // not red/green: phrasing, not a verdict (SATZ-008). Case-insensitive so
    // a word moving to sentence-start doesn't light up as "changed". No
    // markPunctuation — punctuation is a written-grammar concern (see the
    // diff above), phrasing is not.
    const naturalDiff = feedbackResult.naturalVersion
      ? diffTokens(feedbackResult.correctedText, feedbackResult.naturalVersion, {
          caseInsensitive: true,
        })
      : null;
    const renderNaturalToken = onGloss
      ? (t: MarkedToken) => (
          <Glossable
            text={t.text}
            onGloss={(word: string) => onGloss(word, feedbackResult.naturalVersion ?? "")}
            onAdd={onAdd}
          />
        )
      : undefined;

    // IDIOM-002: renders nothing at all when null — no empty card, no
    // placeholder. Present only when a native would genuinely phrase things
    // differently. Sits directly under "Corrected" (or its nothing-to-fix
    // stand-in) and above "What to fix" — the reveal is one more look at the
    // letter's language, not a footnote after the error list.
    const naturalToggle = feedbackResult.naturalVersion && (
      <div className="mt-6 text-center">
        <button
          type="button"
          onClick={() => setShowNatural((v) => !v)}
          className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-white px-5 py-2 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink"
          style={inkShadow}
        >
          {showNatural ? "Hide ▴" : "How would a German write this? ▾"}
        </button>
        {showNatural && feedbackResult.naturalVersion && naturalDiff && (
          <div className="mt-3 rounded-[18px] border-[3px] border-ink bg-paper-warm px-4 py-3 text-left">
            <div className="text-ink">
              <MarkedLetter
                text={feedbackResult.naturalVersion}
                tokens={naturalDiff.corrected}
                mark="blue"
                renderToken={renderNaturalToken}
              />
            </div>
          </div>
        )}
      </div>
    );

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
            {nothingToCorrect ? (
              <div className="rounded-[18px] border-[3px] border-ink bg-paper-warm px-4 py-3">
                <p className="font-body text-[13px] leading-snug text-ink-soft">
                  Nothing to correct — this letter is grammatically clean.
                </p>
              </div>
            ) : (
              <div className="rounded-[18px] border-[3px] border-ink bg-paper-warm px-4 py-3">
                <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                  Corrected
                </p>
                <div className="mt-2 text-ink">
                  <MarkedLetter
                    text={feedbackResult.correctedText}
                    tokens={diff.corrected}
                    mark="green"
                    renderToken={renderCorrectedToken}
                  />
                </div>
              </div>
            )}
          </div>

          {naturalToggle}

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
        </div>

        <Footer onNewLetter={onNewLetter} />
      </div>
    );
  }

  // phase === "writing" | "hints"
  return (
    <div>
      <div className="grid gap-6 lg:grid-cols-2">
        <LetterPanel letter={letter} onGloss={onGloss} onAdd={onAdd} />

        <div
          className="rounded-[28px] border-[3px] border-ink bg-white p-7"
          style={inkShadow}
        >
          {/* The writing brief. Present in both phases — attempt 1 shows it
              undecided (no ticks yet), attempt 2 shows it graded against
              `coveredPoints`. This is instructions, not feedback, so unlike
              the hint message/cards below it never leaves this column. */}
          <div>
            <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
              What to cover
            </p>
            <div className="mt-2">
              <PointsChecklist
                points={letter.points}
                covered={phase === "hints" ? hintResult?.coveredPoints : undefined}
              />
            </div>
          </div>

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

      {/* First-round feedback lives here, below BOTH panels — not stacked
          above the answer column, where it used to sit right on top of
          where the learner's own letter was about to go back in. */}
      {phase === "hints" && hintResult && (
        <div
          className="mt-6 rounded-[28px] border-[3px] border-ink bg-white p-7"
          style={inkShadow}
        >
          <p className="font-display text-[15px] font-black leading-snug text-ink">
            {hintResult.message}
          </p>

          {/* Task 3: replay the first draft with each flagged phrase
              highlighted. Deliberately `firstAttemptText`, the snapshot
              taken the moment attempt 1 was sent — NOT the live `text`
              state. The learner is already editing `text` for attempt 2 by
              the time this renders, so highlighting the live value would
              chase a moving target and light up the wrong words the
              instant a single character changed. */}
          {firstAttemptText && (
            <div className="mt-5">
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                Your draft — look at the marked spots
              </p>
              <p className="mt-2 whitespace-pre-line font-body text-[14px] leading-relaxed text-ink">
                {renderHighlightedDraft(firstAttemptText, hintResult.items)}
              </p>
            </div>
          )}

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
        </div>
      )}

      <Footer onNewLetter={onNewLetter} />
    </div>
  );
}
