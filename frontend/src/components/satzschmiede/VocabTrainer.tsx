"use client";

import { useState } from "react";
import { targetStems, type Card, type CardType } from "./deck";

// Visual identity per word type: badge label + colour. One card shell serves
// every type — the face shows only the badge + the word to produce. Meaning and
// grammar stay hidden until the card flips (recall the gender/usage, don't read
// it off the card).
const TYPE_STYLE: Record<CardType, { label: string; chip: string }> = {
  noun: { label: "Noun", chip: "bg-flag-red text-white" },
  verb: { label: "Verb", chip: "bg-flag-gold text-ink" },
  phrase: { label: "Phrase", chip: "bg-ink text-white" },
};

type Verdict = "correct" | "close" | "revealed";

// The answer side tints the whole card by outcome, so the result reads at a
// glance without scrolling.
const VERDICT_STYLE: Record<
  Verdict,
  { label: string; faceBox: string; tone: string }
> = {
  correct: {
    label: "Correct",
    faceBox: "border-success bg-success-soft",
    tone: "text-success",
  },
  close: {
    label: "Close — not quite",
    faceBox: "border-flag-red bg-flag-red-soft",
    tone: "text-flag-red-deep",
  },
  revealed: {
    label: "Revealed — counts as a miss",
    faceBox: "border-flag-gold bg-flag-gold-soft",
    tone: "text-flag-gold-deep",
  },
};

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// Reflexive verbs are taught by omission: the clue shows the bare verb (no
// `sich`), so a correct sentence has to include a reflexive pronoun. Mock-only
// stand-in for the real examiner's reflexivity check.
const REFLEXIVE_PRONOUNS = ["mich", "dich", "sich", "uns", "euch"];

export default function VocabTrainer({ deck }: { deck: Card[] }) {
  const [index, setIndex] = useState(0);
  const [answer, setAnswer] = useState("");
  const [revealed, setRevealed] = useState(false); // used the hint → a miss
  const [flipped, setFlipped] = useState(false); // showing the answer side

  const total = deck.length;
  const card = deck[index];
  const accent = TYPE_STYLE[card.type];

  // Placeholder verdict: revealing the example is always a miss; otherwise a
  // dumb stem match (targetStems) stands in for the real examiner so both
  // states are designable on any card the DB serves. Reflexive cards
  // additionally require a reflexive pronoun — the learner has to *spot* that
  // the verb is reflexive, since `sich` is never on the clue. Only shown once
  // the card is flipped.
  const usedTarget = targetStems(card).some((s) =>
    answer.toLowerCase().includes(s)
  );
  const usedReflexive =
    !card.reflexive ||
    REFLEXIVE_PRONOUNS.some((p) => new RegExp(`\\b${p}\\b`, "i").test(answer));
  const verdict: Verdict = revealed
    ? "revealed"
    : usedTarget && usedReflexive
      ? "correct"
      : "close";
  const v = VERDICT_STYLE[verdict];

  // Nouns hide the article, reflexive verbs hide `sich` — the same move: a
  // grammatical lead stripped from the clue and restored here on the answer so
  // the learner sees what they were meant to recall.
  const lead = card.article ?? (card.reflexive ? "sich" : undefined);
  const fullForm = lead ? `${lead} ${card.target}` : card.target;

  // Reset the per-card scratch state on every move. Wrap around so browsing the
  // sample deck never dead-ends.
  function goTo(next: number) {
    setIndex((next + total) % total);
    setAnswer("");
    setRevealed(false);
    setFlipped(false);
  }

  return (
    <div className="mx-auto w-full max-w-xl">
      {/* Progress + free browse (reviewer convenience while this is a mock) */}
      <div className="mb-4 flex items-center justify-between">
        <button
          type="button"
          onClick={() => goTo(index - 1)}
          className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red"
        >
          ← Prev
        </button>
        <span className="font-body text-[11px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
          Word {index + 1} of {total}
        </span>
        <button
          type="button"
          onClick={() => goTo(index + 1)}
          className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red"
        >
          Next →
        </button>
      </div>

      {/* The card — word only. Flips in place; the input below never moves. */}
      <div className="flip-scene h-[320px]">
        <div className={`flip-card ${flipped ? "is-flipped" : ""}`}>
          {/* ── Front: the word to produce ──────────────────────────── */}
          <div className="flip-face items-center justify-center rounded-[28px] border-[3px] border-ink bg-white p-7 text-center shadow-[0_6px_0_var(--color-ink)]">
            <span
              className={`inline-flex items-center rounded-full border-[2px] border-ink px-3 py-1 font-body text-[11px] font-black uppercase tracking-[0.18em] ${accent.chip}`}
            >
              {accent.label}
            </span>
            <h2 className="mt-5 font-display text-[clamp(30px,6vw,44px)] font-black leading-[1.05] tracking-tight text-ink">
              {card.target}
            </h2>
          </div>

          {/* ── Back: verdict + the info that was hidden ────────────── */}
          <div
            className={`flip-face flip-back rounded-[28px] border-[3px] p-7 shadow-[0_6px_0_var(--color-ink)] ${v.faceBox}`}
          >
            <div className="flex flex-1 flex-col">
              <p
                className={`font-display text-[16px] font-black uppercase tracking-[0.14em] ${v.tone}`}
              >
                {v.label}
                <span className="ml-2 font-body text-[10px] font-bold tracking-[0.12em] text-ink-faint">
                  placeholder
                </span>
              </p>
              <p className="mt-3 font-display text-[22px] font-black leading-tight text-ink">
                {fullForm}
              </p>
              <dl className="mt-3 flex-1 space-y-2.5">
                <Row label="Meaning" value={card.gloss} />
                {card.note && <Row label="Gender / form" value={card.note} />}
                {card.example && (
                  <Row label="Natural example" value={card.example} />
                )}
              </dl>
            </div>
            <button
              type="button"
              onClick={() => goTo(index + 1)}
              className="btn-3d inline-flex w-full items-center justify-center gap-2 rounded-[20px] border-[3px] border-ink bg-white px-5 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
              style={inkShadow}
            >
              Next word →
            </button>
          </div>
        </div>
      </div>

      {/* ── Separate block, outside the card: prompt + input + actions ─ */}
      <div className={`mt-6 transition-opacity ${flipped ? "opacity-60" : ""}`}>
        <p className="text-center font-body text-[12px] font-semibold uppercase tracking-[0.2em] text-ink-muted">
          Use it in a sentence of your own
        </p>
        <textarea
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          readOnly={flipped}
          rows={3}
          placeholder="Write your sentence in German…"
          className="mt-2.5 w-full resize-none rounded-2xl border-[3px] border-ink bg-paper-warm p-3.5 font-body text-[15px] leading-relaxed text-ink placeholder:text-ink-faint focus:border-flag-red focus:outline-none"
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          {card.example ? (
            <button
              type="button"
              onClick={() => {
                setRevealed(true);
                setFlipped(true);
              }}
              disabled={flipped}
              className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red disabled:opacity-40"
            >
              Reveal example
            </button>
          ) : (
            <span />
          )}
          <button
            type="button"
            onClick={() => setFlipped(true)}
            disabled={flipped || !answer.trim()}
            className="btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-white disabled:pointer-events-none disabled:opacity-40"
            style={redShadow}
          >
            Check answer
          </button>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
        {label}
      </dt>
      <dd className="mt-0.5 font-body text-[16px] leading-relaxed text-ink">
        {value}
      </dd>
    </div>
  );
}
