"use client";

import { useState } from "react";
import { addWord, UnauthorizedError, WordRejectedError } from "./api";
import type { Card } from "./deck";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;

// "Forge your own word" (SATZ-002 Phase 2) — the learner types any German
// word; the backend dedups against the canonical catalog and otherwise runs
// one cheap LLM enrichment call to build the card. Lives at the top of the
// "Add Cards" popup, above the pack gallery.
export default function AddWordForm({
  token,
  onPoolChanged,
  onUnauthorized,
}: {
  token: string;
  onPoolChanged: () => void;
  onUnauthorized: () => void;
}) {
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  // The last successfully forged/linked card, shown inline as confirmation.
  const [forged, setForged] = useState<{ card: Card; added: number } | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const w = word.trim();
    if (!w || busy) return;
    setBusy(true);
    setError(null);
    setForged(null);
    try {
      const res = await addWord(token, w);
      setForged({ card: res.card, added: res.added });
      setWord("");
      if (res.added > 0) onPoolChanged();
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        onUnauthorized();
      } else if (err instanceof WordRejectedError) {
        setError(err.message); // learner-facing sentence from the enricher
      } else {
        setError("The forge hiccuped — try again in a moment.");
      }
    } finally {
      setBusy(false);
    }
  }

  // Restore the hidden grammatical lead on the confirmation line, same move
  // as the trainer's answer side (article for nouns, `sich` for reflexives).
  const lead = forged?.card.article ?? (forged?.card.reflexive ? "sich" : null);

  return (
    <form onSubmit={handleSubmit}>
      <h3 className="text-center font-display text-[18px] font-black leading-tight text-ink">
        Forge your own word
      </h3>
      <p className="mx-auto mt-1 max-w-[340px] text-center font-body text-[13px] leading-relaxed text-ink-soft">
        Type any German word — we build the card for you.
      </p>

      <div className="mt-4 flex gap-3">
        <input
          value={word}
          onChange={(e) => setWord(e.target.value)}
          disabled={busy}
          placeholder="e.g. Feierabend"
          className="min-w-0 flex-1 rounded-2xl border-[3px] border-ink bg-paper-warm px-4 py-2.5 font-body text-[15px] text-ink placeholder:text-ink-faint focus:border-flag-red focus:outline-none disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !word.trim()}
          className="btn-3d inline-flex shrink-0 items-center rounded-[16px] border-[3px] border-flag-red-deep bg-flag-red px-5 py-2 font-display text-[13px] font-black uppercase tracking-[0.14em] text-white disabled:pointer-events-none disabled:opacity-40"
          style={redShadow}
        >
          {busy ? "Forging…" : "Add word"}
        </button>
      </div>

      {error && (
        <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-red-deep">
          {error}
        </p>
      )}
      {forged && (
        <p className="mt-3 text-center font-body text-[13px] font-semibold text-ink">
          <span className="font-black">
            {lead ? `${lead} ${forged.card.target}` : forged.card.target}
          </span>
          {" — "}
          {forged.card.gloss}
          <span className="ml-2 font-body text-[11px] font-black uppercase tracking-[0.14em] text-success">
            {/* Verbs land as a pair (present + spoken past), so say so. */}
            {forged.added > 1
              ? `${forged.added} cards ✓`
              : forged.added > 0
                ? "added ✓"
                : "already in your pool"}
          </span>
        </p>
      )}
    </form>
  );
}
