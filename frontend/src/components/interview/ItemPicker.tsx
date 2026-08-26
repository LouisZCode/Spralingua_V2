"use client";

import type { InterviewItemSummary } from "./api";

const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// Item picker — one card per recording in the learner's personal audio pool.
// Company + level + chunk count always show; the interviewer line is
// omitted entirely when the backend couldn't parse a name out of the
// recording's dir name (interviewer === "", see interview/display.py).
export default function ItemPicker({
  items,
  error,
  onPick,
}: {
  items: InterviewItemSummary[] | null; // null = loading
  error: boolean;
  onPick: (id: string) => void;
}) {
  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-8 text-center">
        <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
          Interview practice
        </p>
        <h1 className="mt-3 font-display text-[clamp(26px,4.4vw,38px)] font-black leading-[1.05] tracking-tight text-ink">
          Real interviews, in two rounds
        </h1>
        <p className="mx-auto mt-4 max-w-xl font-body text-[15px] leading-relaxed text-ink-soft">
          Listen to one chunk of a real interview and retell what you heard —
          then read it and answer the question yourself, out loud.
        </p>
      </div>

      {error ? (
        <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
          Couldn&apos;t load your interviews — is the backend running?
        </p>
      ) : items === null ? (
        <p className="text-center font-body text-[12px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
          Loading…
        </p>
      ) : items.length === 0 ? (
        <div className="rounded-[28px] border-[3px] border-ink-faint bg-paper-warm p-8 text-center">
          <p className="font-display text-[18px] font-black tracking-tight text-ink">
            No interviews in your pool yet
          </p>
          <p className="mt-2 font-body text-[14px] leading-relaxed text-ink-soft">
            This exercise practices on interviews recorded specifically for
            you — there&apos;s nothing here until one is added.
          </p>
        </div>
      ) : (
        <div className="grid gap-5 sm:grid-cols-2">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onPick(item.id)}
              className="btn-3d flex flex-col items-start gap-3 rounded-3xl border-[3px] border-line bg-card px-6 py-6 text-left transition hover:bg-paper-warm"
              style={inkShadow}
            >
              <div className="flex w-full items-start justify-between gap-3">
                <span className="font-display text-[20px] font-black leading-tight text-ink">
                  {item.company}
                </span>
                {item.level && (
                  <span className="shrink-0 rounded-full border-2 border-line bg-flag-gold-soft px-2.5 py-0.5 font-body text-[11px] font-black uppercase tracking-[0.1em] text-ink">
                    {item.level}
                  </span>
                )}
              </div>
              {item.interviewer && (
                <span className="font-body text-[13px] font-semibold text-ink-muted">
                  with {item.interviewer}
                </span>
              )}
              <span className="mt-1 font-body text-[12px] font-bold uppercase tracking-[0.16em] text-ink-faint">
                {item.nChunks} {item.nChunks === 1 ? "chunk" : "chunks"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
