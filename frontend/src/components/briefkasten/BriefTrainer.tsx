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
// Generic over the item shape: attempt 1 feeds it HintItems, attempt 2 feeds
// it the judge's Corrections re-keyed to `text` — same span logic for both.
type ResolvedMatch<T = HintItem> = { start: number; end: number; item: T };

function resolveMatches<T extends { text: string }>(
  text: string,
  items: T[]
): ResolvedMatch<T>[] {
  const candidates = items
    .map((item) => {
      const m = findQuotedPhrase(text, item.text);
      return m ? { ...m, item } : null;
    })
    .filter((m): m is ResolvedMatch<T> => m !== null)
    .sort((a, b) => a.start - b.start);

  const accepted: ResolvedMatch<T>[] = [];
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

// ─── Attempt-2 feedback surface (BRIEF-009) ───
// The final verdict used to render the letter twice ("What you wrote" vs
// "Corrected") and then repeat every correction in a card list below. Now the
// learner's own submitted letter is the whole surface, one more time: the
// phrases the judge corrected are highlighted in place, and hovering (or
// tapping, on touch) one shows THE FIX — the corrected German plus the rule —
// since this pass, unlike attempt 1's hints, is allowed to reveal answers.
//
// Read-only, so there is no textarea mirror here: the marks are real <mark>
// elements in rendered text (whitespace-pre-line keeps the letter's own line
// breaks and shape), and the tooltip is positioned from the mark's DOM rect —
// the same fixed-position, left-clamped, flip-below treatment HintEditor
// uses, so both feedback phases share one visual language.
type CorrectionMatch = ResolvedMatch<Explanation & { text: string }>;

// Within a corrected phrase, only the words that actually differ from what
// the learner wrote light up — "das ist normal denke ich" → "das ist normal,
// denke ich" highlights just "normal,", not the whole phrase. A word-level
// LCS between the written and corrected tokens decides which corrected words
// are new/changed; identical words ride along in plain ink so the change
// jumps out instead of drowning in green. Phrases are short, so the O(n·m)
// table is nothing.
function changedWords(written: string, correction: string): boolean[] {
  const a = written.split(/\s+/).filter(Boolean);
  const b = correction.split(/\s+/).filter(Boolean);
  const dp: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0)
  );
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      dp[i][j] =
        a[i] === b[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const changed: boolean[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      changed.push(false);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i++; // word dropped from the correction — nothing to show
    } else {
      changed.push(true); // word added/changed in the correction
      j++;
    }
  }
  while (j < b.length) {
    changed.push(true);
    j++;
  }
  return changed;
}

function CorrectedLetter({
  text,
  matches,
}: {
  text: string;
  matches: CorrectionMatch[];
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; flip: boolean } | null>(
    null
  );

  // Touch has no hover: on a hover-capable pointer the marks open on
  // mouseenter (and click is just a re-open), on touch the tap itself
  // toggles. Computed once — a device doesn't grow a mouse mid-letter.
  const canHover = useMemo(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(hover: hover)").matches,
    []
  );

  const openTip = (idx: number) => {
    const markEl = rootRef.current?.querySelector<HTMLElement>(
      `[data-mark="${idx}"]`
    );
    if (!markEl) return;
    const r = markEl.getBoundingClientRect();
    setActive(idx);
    // Above the phrase by default; below when there's no headroom for it.
    setTip({
      x: r.left,
      y: r.top < 130 ? r.bottom + 8 : r.top - 8,
      flip: r.top < 130,
    });
  };

  const closeTip = () => {
    setActive(null);
    setTip(null);
  };

  // Any scroll or resize orphans a fixed-position tooltip — dismiss it
  // rather than chase the mark. Escape closes too, same as the hint editor.
  useEffect(() => {
    if (active === null) return;
    const dismiss = () => {
      setActive(null);
      setTip(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("keydown", onKey);
    };
  }, [active]);

  // Same alternating plain-text / marked-chunk construction HintEditor's
  // backdrop uses — the slices carry the letter's newlines through intact.
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  matches.forEach((m, i) => {
    if (m.start > cursor) nodes.push(text.slice(cursor, m.start));
    nodes.push(
      <mark
        key={i}
        data-mark={i}
        onMouseEnter={canHover ? () => openTip(i) : undefined}
        onMouseLeave={canHover ? closeTip : undefined}
        onClick={(e) => {
          e.stopPropagation();
          if (!canHover && active === i && tip) {
            closeTip();
            return;
          }
          openTip(i);
        }}
        className={`cursor-help rounded-[4px] px-0.5 text-ink ${
          i === active ? "bg-flag-gold" : "bg-flag-gold-soft"
        }`}
      >
        {text.slice(m.start, m.end)}
      </mark>
    );
    cursor = m.end;
  });
  if (cursor < text.length) nodes.push(text.slice(cursor));

  const current = active !== null ? matches[active] : undefined;
  return (
    <div ref={rootRef} onClick={closeTip} className="relative">
      <div className="whitespace-pre-line rounded-[18px] border-[3px] border-line bg-card p-4 font-body text-[15px] leading-relaxed text-ink">
        {nodes}
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
          <p className="font-body text-[15px] leading-snug text-ink">
            <span className="font-bold text-success">→ </span>
            {(() => {
              // Spotlight only what changed: words the LCS match keeps are
              // plain ink, words the judge added or altered go bold green.
              const written = text.slice(current.start, current.end);
              const flags = changedWords(written, current.item.correction);
              const words = current.item.correction.split(/\s+/).filter(Boolean);
              return words.map((w, k) => (
                <span key={k}>
                  {k > 0 ? " " : null}
                  {flags[k] ? (
                    <span className="font-bold text-success">{w}</span>
                  ) : (
                    w
                  )}
                </span>
              ));
            })()}
          </p>
          <p className="mt-1 font-body text-[12px] leading-snug text-ink-muted">
            {current.item.why}
          </p>
        </div>
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

  // BRIEF-009: the attempt-2 verdict as spans in the submitted letter. The
  // judge quotes each correction's `error` "exactly from the revised letter"
  // (briefkasten/judge.py), so the same tolerant matcher that places attempt
  // 1's hints places these; a quote that can't be found is skipped silently —
  // a missed highlight is invisible, a wrong one is not acceptable in the
  // learner's own writing. `text` is frozen here: the feedback phase has no
  // editor, so this resolves once per render with nothing to chase.
  const correctionMatches = useMemo(
    () =>
      feedbackResult
        ? resolveMatches(
            text,
            feedbackResult.explanations.map((e) => ({ ...e, text: e.error }))
          )
        : [],
    [text, feedbackResult]
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
    // BRIEF-009: the letter, the score, and the German-way button. The diff
    // machinery, the parallel boxes and the card lists this phase used to
    // render are gone — the corrections now live ON the letter in
    // CorrectedLetter above.

    // IDIOM-002, collapsed (BRIEF-009): the Germanize call already ran
    // server-side alongside the attempt-2 judge, so this is a pure reveal —
    // the card starts shut and the button opens it, for learners who want
    // the native phrasing. `naturalVersion: null` renders nothing at all:
    // the praise note it used to show was one more card on a screen this
    // pass just decluttered.
    const naturalToggle = feedbackResult.naturalVersion ? (
      <GermanWay
        text={feedbackResult.correctedText}
        verb="write"
        value={{ natural: feedbackResult.naturalVersion }}
        onGloss={onGloss}
        onAdd={onAdd}
      />
    ) : null;

    return (
      <div>
        <div
          className="mx-auto max-w-[560px] rounded-[28px] border-[3px] border-line bg-card p-7"
          style={inkShadow}
        >
          <div className="text-center">
            <span className="inline-flex items-center rounded-full border-[2px] border-line bg-ink-fill px-5 py-2 font-display text-[20px] font-black uppercase tracking-[0.1em] text-on-fill">
              {feedbackResult.score}/100
            </span>
            <p className="mt-6 font-display text-[18px] font-black leading-snug text-ink">
              {feedbackResult.feedback}
            </p>
          </div>

          {/* BRIEF-009: the letter IS the feedback. The learner's own final
              draft, in its own shape, with the judge's corrections marked in
              place — hover/tap a marked phrase for the fix and its rule. The
              parallel "What you wrote"/"Corrected" boxes, the "What to fix"
              card list and the "Focus for next time" box are all gone: their
              content lives in the marks or was noise. */}
          <div className="mt-9">
            <CorrectedLetter text={text} matches={correctionMatches} />
          </div>

          {naturalToggle}
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
          {/* The writing brief. Present in both phases. In the writing phase
              it's undecided instructions; in the hints phase BRIEF-008 keeps
              the coverage verdict here — green check / red mark per point —
              while the phrase-level feedback itself lives on the letter in
              HintEditor below. */}
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
