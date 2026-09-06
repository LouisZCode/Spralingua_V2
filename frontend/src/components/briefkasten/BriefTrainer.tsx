"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
import GermanWay from "../shared/GermanWay";
import type { GlossInfo } from "../satzschmiede/api";
import { InsufficientCoinsError } from "@/lib/coins";
import { OutOfCoinsPanel, refreshCoins } from "../shared/Coins";
import { safeMessage } from "../shared/copy";
// PAY-002 note: Briefkasten charges the LETTER, not the attempt — the 15
// coins are taken at GET /briefkasten/letter and both attempts ride free on
// that ticket, so the real out-of-coins surface is Briefkasten.tsx's mint.
// The handlers below stay as a defensive net in case pricing ever moves to
// the attempt, and cost nothing while it hasn't.

type Phase = "writing" | "hints" | "feedback";

const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

const CATEGORY_LABEL: Record<HintItem["category"], string> = {
  grammatik: "Grammar",
  wortstellung: "Word order",
  wortschatz: "Vocabulary",
  rechtschreibung: "Spelling",
};
// BRIEF-008: kept for the attempt-1 hint tooltip. The category-grouped hint
// list it used to index is gone — the hint now lives ON the highlighted
// phrase in the learner's own letter (see HintEditor below).

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

// BRIEF-008: resolves every hint's quoted phrase to a span in the LIVE text,
// dropping any span that overlaps one already accepted (earliest-start wins).
// Runs against the current editor value on every keystroke — when the learner
// rewrites a flagged phrase, its match (and with it the highlight and the
// hint tooltip) simply disappears: the mark set shrinks as they fix things.
type ResolvedMatch = { start: number; end: number; item: HintItem };

function resolveMatches(text: string, items: HintItem[]): ResolvedMatch[] {
  const candidates = items
    .map((item) => {
      const m = findQuotedPhrase(text, item.text);
      return m ? { ...m, item } : null;
    })
    .filter((m): m is ResolvedMatch => m !== null)
    .sort((a, b) => a.start - b.start);

  const accepted: ResolvedMatch[] = [];
  for (const m of candidates) {
    const prev = accepted[accepted.length - 1];
    if (prev && m.start < prev.end) continue; // overlaps — discard
    accepted.push(m);
  }
  return accepted;
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
                  ? "border-line/25 text-transparent"
                  : isCovered
                    ? "border-success bg-success-fill text-on-fill"
                    : "border-flag-red bg-card text-flag-red"
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
  // BRIEF-002: threaded straight through to each line's MarkedText, same
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
      className="rounded-[28px] border-[3px] border-line bg-card p-7"
      style={inkShadow}
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          From {letter.sender.name} · {letter.sender.relation}
        </p>
        <span className="inline-flex items-center rounded-full border-[2px] border-line bg-card px-3 py-0.5 font-body text-[10px] font-black uppercase tracking-[0.16em] text-ink-muted">
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

// `newLetter` — the red advance button, shown ONLY on the final feedback
// screen. It was hidden between attempt 1 and attempt 2 already (user request,
// 2026-08-17): with the hints on screen the only next step is to revise the
// draft ABOVE and send it again, and a fresh-letter button at the foot of that
// page read as "the next thing to click" and threw the draft away.
//
// PAY-002: it is now hidden on the untouched writing screen too, where it used
// to offer a reroll. A round is a fixed, pre-paid number of letters — rerolling
// spent one of them on a letter the learner never answered, and on the (default)
// one-letter round it ended the sitting outright. Rerolling also let a learner
// shop for an easier prompt; staying with the letter you were dealt is the
// exercise. The way out is ← Back to practice.
//
// `label` — "Next letter" mid-round, "Finish round" on the last one, so the
// button says what it actually does rather than implying an endless supply.
function Footer({
  onNewLetter,
  newLetter = true,
  label = "Next letter",
}: {
  onNewLetter: () => void;
  newLetter?: boolean;
  label?: string;
}) {
  return (
    <div className="mt-8 flex items-center justify-center gap-5">
      {newLetter && (
        <button
          type="button"
          onClick={onNewLetter}
          className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-red-line bg-flag-red-fill px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-on-fill"
          style={redShadow}
        >
          {label}
        </button>
      )}
      <Link
        href="/practice"
        className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
      >
        ← Back to practice
      </Link>
    </div>
  );
}

// ─── Attempt-1 feedback surface (BRIEF-008) ───
// The hints phase used to repeat every hint in category boxes below the
// draft, making the learner ping-pong between the list and the text they
// were fixing. Now their own letter IS the whole feedback surface: flagged
// phrases are highlighted in place and clicking/tapping into one opens that
// hint as a tooltip right there — the hint only, never the fix, matching
// the hint judge's contract (briefkasten/judge.py).
//
// A textarea can't render inline marks, so this is the classic
// highlight-within-textarea trick: a pixel-mirrored backdrop div (same
// font, padding, line-height, wrapping) sits underneath a transparent-text
// textarea whose caret stays visible. The backdrop is re-rendered from the
// same controlled `value` on every keystroke, so marks stay glued to their
// phrases as the learner edits.
//
// Hover doesn't exist on touch, so activation is caret-based: a click/tap
// (or arrow-key move) that lands the caret inside a marked range opens
// that hint; typing, scrolling, or Escape closes it. The tooltip is
// positioned from the backdrop mark's DOM rect (the backdrop mirrors the
// wrapping, so its rect IS where the phrase sits on screen) and rendered
// position:fixed so the editor's overflow can never clip it.
function HintEditor({
  value,
  onChange,
  matches,
}: {
  value: string;
  onChange: (next: string) => void;
  matches: ResolvedMatch[];
}) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; flip: boolean } | null>(
    null
  );

  const closeTip = () => {
    setActive(null);
    setTip(null);
  };

  // Mirror the textarea's internal scroll onto the backdrop so the marks
  // stay aligned with their phrases when the letter outgrows the box.
  const syncScroll = () => {
    if (backdropRef.current && taRef.current) {
      backdropRef.current.scrollTop = taRef.current.scrollTop;
    }
  };

  const updateFromCaret = () => {
    const ta = taRef.current;
    if (!ta) return;
    const pos = ta.selectionStart;
    const idx = matches.findIndex((m) => pos >= m.start && pos <= m.end);
    if (idx === -1) {
      closeTip();
      return;
    }
    if (idx === active && tip) return; // already showing this one
    const markEl = backdropRef.current?.querySelector<HTMLElement>(
      `[data-mark="${idx}"]`
    );
    if (!markEl) {
      closeTip();
      return;
    }
    const r = markEl.getBoundingClientRect();
    setActive(idx);
    // Above the phrase by default; below when there's no headroom for it.
    setTip({
      x: r.left,
      y: r.top < 130 ? r.bottom + 8 : r.top - 8,
      flip: r.top < 130,
    });
  };

  // Any scroll or resize orphans a fixed-position tooltip — dismiss it
  // rather than chase the mark. Capture-phase scroll also catches the
  // textarea's own scrolling (its onScroll dismisses explicitly too).
  useEffect(() => {
    if (active === null) return;
    const dismiss = () => {
      setActive(null);
      setTip(null);
    };
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [active]);

  // Same alternating plain-text / marked-chunk construction the old frozen
  // replay used, now against the live value. The trailing newline keeps the
  // backdrop's scroll height honest when the letter ends in a line break.
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  matches.forEach((m, i) => {
    if (m.start > cursor) nodes.push(value.slice(cursor, m.start));
    nodes.push(
      <mark
        key={i}
        data-mark={i}
        className={`rounded-[4px] px-0.5 text-ink ${
          i === active ? "bg-flag-gold" : "bg-flag-gold-soft"
        }`}
      >
        {value.slice(m.start, m.end)}
      </mark>
    );
    cursor = m.end;
  });
  if (cursor < value.length) nodes.push(value.slice(cursor));
  const current = active !== null ? matches[active] : undefined;
  return (
    <div className="relative">
      <div className="relative h-[300px] min-h-[200px] resize-y overflow-hidden rounded-[18px] border-[3px] border-line bg-card focus-within:border-red-line">
        <div
          ref={backdropRef}
          aria-hidden
          className="absolute inset-0 overflow-hidden whitespace-pre-wrap break-words p-4 font-body text-[15px] leading-relaxed text-ink"
        >
          {nodes}
          {"\n"}
        </div>
        <textarea
          ref={taRef}
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            closeTip();
          }}
          onClick={updateFromCaret}
          // Navigation keys only: character keys re-run through onChange,
          // which dismisses — re-running here would instantly re-open the
          // tooltip on the stale match the keystroke is busy rewriting.
          onKeyUp={(e) => {
            if (
              [
                "ArrowLeft",
                "ArrowRight",
                "ArrowUp",
                "ArrowDown",
                "Home",
                "End",
                "PageUp",
                "PageDown",
              ].includes(e.key)
            ) {
              updateFromCaret();
            }
          }}
          onScroll={() => {
            syncScroll();
            closeTip();
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") closeTip();
          }}
          placeholder="Schreib deine Antwort auf Deutsch…"
          className="absolute inset-0 h-full w-full resize-none overflow-y-auto bg-transparent p-4 font-body text-[15px] leading-relaxed text-transparent caret-ink outline-none [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        />
      </div>
      {current && tip && (
        <div
          role="status"
          className="fixed z-50 w-[280px] rounded-[14px] border-[3px] border-line bg-card p-3 shadow-lg"
          style={{
            top: tip.y,
            left: Math.max(
              12,
              Math.min(
                tip.x,
                (typeof window !== "undefined" ? window.innerWidth : 400) - 292
              )
            ),
            transform: tip.flip ? undefined : "translateY(-100%)",
          }}
        >
          <p className="font-body text-[10px] font-black uppercase tracking-[0.2em] text-flag-red">
            {CATEGORY_LABEL[current.item.category]}
          </p>
          <p className="mt-1 font-body text-[13px] leading-snug text-ink">
            {current.item.hint}
          </p>
        </div>
      )}
    </div>
  );
}

export default function BriefTrainer({
  letter,
  onAttempt,
  onNewLetter,
  isLastLetter = false,
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
  // Advance the round: the parent counts this letter as done and remounts
  // this component with the next one (or shows the round-complete screen).
  onNewLetter: () => void;
  // True on the round's final letter — flips the advance button's label from
  // "Next letter" to "Finish round".
  isLastLetter?: boolean;
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
  const [insufficient, setInsufficient] = useState<{ needed: number; available: number } | null>(null);

  const words = wordCount(text);
  const inRange = words >= letter.wordTarget.min && words <= letter.wordTarget.max;
  const canSubmit = text.trim().length > 0 && !submitting;

  // BRIEF-008: the attempt-1 highlights, resolved against the LIVE text on
  // every keystroke. When the learner rewrites a flagged phrase, its match
  // (and with it the highlight and its hint) simply disappears — the mark
  // set shrinking as they fix things. Overlapping judge quotes resolve
  // earliest-start-wins, exactly like the frozen replay they replace.
  const hintMatches = useMemo(
    () => (hintResult ? resolveMatches(text, hintResult.items) : []),
    [text, hintResult]
  );

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
      if (err instanceof InsufficientCoinsError) {
        setInsufficient({ needed: err.needed, available: err.available });
        refreshCoins();
      } else {
        setFailed(
          safeMessage(err, "Couldn't check that — try again in a moment.")
        );
      }
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
      if (err instanceof InsufficientCoinsError) {
        setInsufficient({ needed: err.needed, available: err.available });
        refreshCoins();
      } else {
        setFailed(
          safeMessage(err, "Couldn't check that — try again in a moment.")
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (phase === "feedback" && feedbackResult) {
    // BRIEF-002: `markPunctuation` on — the learner typed every character of
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

    // SATZ-018/BRIEF-002: same per-token Glossable override GermanWay and
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

    // IDIOM-002: present only when a native would genuinely phrase things
    // differently. Sits directly under "Corrected" (or its nothing-to-fix
    // stand-in) and above "What to fix" — the reveal is one more look at the
    // letter's language, not a footnote after the error list. When null, a
    // small praise note takes this same spot instead of rendering nothing —
    // reuses the nothingToCorrect card treatment (paper-warm rounded card,
    // ink-soft text) so it reads as one more quiet confirmation, not a new
    // visual language, and doesn't compete with the "Corrected" block above.
    //
    // The rewrite itself already ran server-side (briefkasten/germanizer.py,
    // alongside feedback_pass) — GermanWay gets it via `value`, not a fetch,
    // and `autoExpand` opens the card without a click. Diffed against the
    // CORRECTED letter (not the learner's raw attempt): this reveal answers
    // "how would a German phrase the fixed version", so the comparison base
    // is the fix, not the mistake.
    const naturalToggle = feedbackResult.naturalVersion ? (
      <GermanWay
        text={feedbackResult.correctedText}
        value={{ natural: feedbackResult.naturalVersion }}
        autoExpand
        onGloss={onGloss}
        onAdd={onAdd}
      />
    ) : (
      <div className="mt-6 rounded-[18px] border-[3px] border-line bg-paper-warm px-4 py-3 text-center">
        <p className="font-body text-[13px] leading-snug text-ink-soft">
          {feedbackResult.score >= 90
            ? "Nothing to Germanize — this letter was German through and through. Perfect!"
            : feedbackResult.score >= 70
              ? "Nothing to Germanize — this letter was German enough. Well done!"
              : "Nothing extra to Germanize — focus on the fixes above and keep working on it!"}
        </p>
      </div>
    );

    return (
      <div>
        <div
          className="mx-auto max-w-[560px] rounded-[28px] border-[3px] border-line bg-card p-7"
          style={inkShadow}
        >
          <div className="text-center">
            <span className="inline-flex items-center rounded-full border-[2px] border-line bg-ink-fill px-4 py-1.5 font-display text-[13px] font-black uppercase tracking-[0.14em] text-on-fill">
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
            <div className="rounded-[18px] border-[3px] border-line bg-card px-4 py-3">
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                What you wrote
              </p>
              <div className="mt-2 text-ink-soft">
                <MarkedLetter text={text} tokens={diff.attempt} mark="red" />
              </div>
            </div>
            {nothingToCorrect ? (
              <div className="rounded-[18px] border-[3px] border-line bg-paper-warm px-4 py-3">
                <p className="font-body text-[13px] leading-snug text-ink-soft">
                  Nothing to correct — this letter is grammatically clean.
                </p>
              </div>
            ) : (
              <div className="rounded-[18px] border-[3px] border-line bg-paper-warm px-4 py-3">
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
                    className="rounded-[16px] border-[3px] border-line bg-card px-4 py-3"
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
            <div className="mt-6 rounded-[18px] border-[3px] border-line bg-card px-4 py-4">
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

        <Footer
          onNewLetter={onNewLetter}
          label={isLastLetter ? "Finish round" : "Next letter"}
        />
      </div>
    );
  }

  // phase === "writing" | "hints"
  return (
    <div>
      <div className="grid gap-6 lg:grid-cols-2">
        <LetterPanel letter={letter} onGloss={onGloss} onAdd={onAdd} />

        <div
          className="rounded-[28px] border-[3px] border-line bg-card p-7"
          style={inkShadow}
        >
          {/* The writing brief. Present in both phases. This is
              instructions, not feedback — BRIEF-008 removed the graded
              coverage ticks from the hints phase: the feedback surface is
              now the learner's own letter with its inline hints, and the
              checklist stays a plain, undecided list throughout. */}
          <div>
            <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
              What to cover
            </p>
            <div className="mt-2">
              <PointsChecklist points={letter.points} />
            </div>
          </div>

          <div className="mt-5">
            {phase === "hints" ? (
              // BRIEF-008: in the hints phase this box IS the feedback
              // surface — the learner's own letter with the flagged phrases
              // highlighted in place and their hints on click/tap, editable
              // right here. The edited text is what "Send the improved
              // version" submits; there is no separate revise box anymore.
              <HintEditor value={text} onChange={setText} matches={hintMatches} />
            ) : (
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={11}
                placeholder="Schreib deine Antwort auf Deutsch…"
                className="w-full resize-y rounded-[18px] border-[3px] border-line bg-card p-4 font-body text-[15px] leading-relaxed text-ink outline-none focus:border-red-line"
              />
            )}
            <p
              className={`mt-2 font-body text-[11px] font-bold uppercase tracking-[0.16em] ${
                inRange ? "text-success" : "text-ink-muted"
              }`}
            >
              {words} {words === 1 ? "word" : "words"} · aim for{" "}
              {letter.wordTarget.min}–{letter.wordTarget.max}
            </p>
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

          <div className="mt-5 flex justify-center">
            {phase === "writing" ? (
              <button
                type="button"
                disabled={!canSubmit}
                onClick={submitFirst}
                className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-red-line bg-flag-red-fill px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-on-fill disabled:cursor-not-allowed disabled:opacity-40"
                style={redShadow}
              >
                {submitting ? "Sending…" : "Send letter"}
              </button>
            ) : (
              <button
                type="button"
                disabled={!canSubmit}
                onClick={submitSecond}
                className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-red-line bg-flag-red-fill px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-on-fill disabled:cursor-not-allowed disabled:opacity-40"
                style={redShadow}
              >
                {submitting ? "Sending…" : "Send the improved version"}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* BRIEF-008: the old first-round feedback card (judge message, frozen
          draft replay, category-grouped hint list) is gone. The hints now
          live ON the learner's own letter inside HintEditor above — one
          surface, highlighted and editable, nothing to scroll between. */}

      <Footer onNewLetter={onNewLetter} newLetter={false} />
    </div>
  );
}
