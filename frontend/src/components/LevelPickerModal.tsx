"use client";

import { useState } from "react";
import { createPortal } from "react-dom";
import type { Level } from "./auth/AuthContext";

// LEVEL-001: the one-time "where are you?" question.
//
// Rounds used to be assembled with no notion of the learner's level, so a B1
// learner got A1 items (present-tense verb endings, sein-vs-haben) mixed into
// every Flow. This answer sets the ceiling on what they're served; the error
// ledger decides which below-level patterns still come back.
//
// Four buckets, not six — a self-declared level isn't accurate past that.
// B1 and B2+ are split even though the grammar taxonomy tops out at B1,
// because content-leveled exercises need the finer distinction. "Not sure"
// is a real answer that stores null and keeps serving everything, so
// nobody is ever stuck behind a guess they made once. Portaled to <body>
// for the same reason
// QuietRoomModal is: the rise-in transforms on the page below would otherwise
// trap a fixed overlay.

const OPTIONS: { level: Level; title: string; blurb: string }[] = [
  {
    level: "A1",
    title: "A1 · Just starting",
    blurb: "Present tense, basic word order, der/die/das.",
  },
  {
    level: "A2",
    title: "A2 · Getting comfortable",
    blurb: "Cases, past tense, prepositions that trip you up.",
  },
  {
    level: "B1",
    title: "B1 · Fairly confident",
    blurb: "Subordinate clauses, relative clauses, tense colouring.",
  },
  {
    level: "B2+",
    title: "B2+ · At home in German",
    blurb: "Complex German is comfortable — you want fine-tuning and natural phrasing, not simplification.",
  },
];

export default function LevelPickerModal({
  onDone,
}: {
  onDone: (level: Level | null) => Promise<void> | void;
}) {
  const [saving, setSaving] = useState<Level | "skip" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function choose(level: Level | null) {
    setSaving(level ?? "skip");
    setError(null);
    try {
      await onDone(level);
    } catch {
      setError("Couldn't save that — try again.");
      setSaving(null);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[100] grid place-items-center bg-scrim/60 p-6 backdrop-blur-sm">
      <div className="lift-panel relative w-full max-w-md rounded-[28px] border-[3px] border-line bg-elevated p-8 text-center">
        <h2 className="font-display text-[24px] font-black leading-tight text-ink">
          Where are you with German?
        </h2>
        <p className="mx-auto mt-3 max-w-[320px] font-body text-[14px] leading-relaxed text-ink-soft">
          So we stop handing you exercises you finished years ago. You can
          change this whenever you like.
        </p>

        <div className="mt-6 flex flex-col gap-3">
          {OPTIONS.map((o) => (
            <button
              key={o.level}
              type="button"
              disabled={saving !== null}
              onClick={() => choose(o.level)}
              className="btn-3d rounded-[20px] border-[3px] border-line bg-paper px-5 py-4 text-left disabled:opacity-60"
            >
              <span className="block font-display text-[15px] font-black uppercase tracking-[0.12em] text-ink">
                {saving === o.level ? "Saving…" : o.title}
              </span>
              <span className="mt-1 block font-body text-[13px] leading-snug text-ink-soft">
                {o.blurb}
              </span>
            </button>
          ))}
        </div>

        {error ? (
          <p className="mt-4 font-body text-[13px] text-flag-red">{error}</p>
        ) : null}

        <button
          type="button"
          disabled={saving !== null}
          onClick={() => choose(null)}
          className="mt-5 font-body text-[13px] uppercase tracking-[0.14em] text-ink-soft underline underline-offset-4 disabled:opacity-60"
        >
          Not sure — show me everything
        </button>
      </div>
    </div>,
    document.body,
  );
}
