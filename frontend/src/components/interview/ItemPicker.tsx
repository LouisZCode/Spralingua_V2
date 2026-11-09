"use client";

import type { InterviewItemSummary } from "./api";
import { loadError } from "../shared/copy";

const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// Item picker — the learner's personal audio pool, grouped by kind:
// real job interviews (kind=interview, the original corpus) and daily-call
// batches (kind=daily_call, e.g. the Sopra Steria mentor calls — one item
// per dated call, so picking a date shows that call's chunks). Grouping key
// comes from audio_items.source via GET /interview/items.
export default function ItemPicker({
  items,
  error,
  onPick,
}: {
  items: InterviewItemSummary[] | null; // null = loading
  error: boolean;
  onPick: (id: string) => void;
}) {
  // Split once, upstream of every render branch, so the header copy and the
  // grids below agree on what exists.
  const interviews =
    items?.filter((i) => i.kind !== "daily_call") ?? null;
  // Dated titles ("DailyCall 2026-11-06 checkup") sort newest-first as
  // plain strings; the "DailyCall " prefix is stripped for the card title.
  const dailyCalls =
    items
      ?.filter((i) => i.kind === "daily_call")
      .sort((a, b) => b.title.localeCompare(a.title)) ?? null;
  const onlyDailyCalls =
    dailyCalls !== null && dailyCalls.length > 0 &&
    (interviews === null || interviews.length === 0);

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-8 text-center">
        <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
          {onlyDailyCalls ? "Call practice" : "Interview practice"}
        </p>
        <h1 className="mt-3 font-display text-[clamp(26px,4.4vw,38px)] font-black leading-[1.05] tracking-tight text-ink">
          {onlyDailyCalls ? "Daily calls, in two rounds" : "Real interviews, in two rounds"}
        </h1>
        <p className="mx-auto mt-4 max-w-xl font-body text-[15px] leading-relaxed text-ink-soft">
          {onlyDailyCalls
            ? "Listen to one chunk of a real work call and retell what you heard — then read it and answer out loud."
            : "Listen to one chunk of a real interview and retell what you heard — then read it and answer the question yourself, out loud."}
        </p>
      </div>

      {error ? (
        <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
          {loadError("your recordings")}
        </p>
      ) : items === null ? (
        <p className="text-center font-body text-[12px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
          Loading…
        </p>
      ) : items.length === 0 ? (
        <div className="rounded-[28px] border-[3px] border-ink-faint bg-paper-warm p-8 text-center">
          <p className="font-display text-[18px] font-black tracking-tight text-ink">
            No recordings in your pool yet
          </p>
          <p className="mt-2 font-body text-[14px] leading-relaxed text-ink-soft">
            This exercise practices on real recorded conversations —
            there&apos;s nothing here until one is added.
          </p>
        </div>
      ) : (
        <>
          {interviews && interviews.length > 0 && (
            <div className="grid gap-5 sm:grid-cols-2">
              {interviews.map((item) => (
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
                  <span className="mt-1 font-body text-[12px] font-bold uppercase tracking-[0.16em] text-ink-muted">
                    {item.nChunks} {item.nChunks === 1 ? "chunk" : "chunks"}
                  </span>
                </button>
              ))}
            </div>
          )}

          {dailyCalls && dailyCalls.length > 0 && (
            <section className={interviews && interviews.length > 0 ? "mt-12" : undefined}>
              <div className="mb-4">
                <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
                  Daily calls
                </p>
                <p className="mt-1 font-body text-[14px] leading-relaxed text-ink-soft">
                  One card per call — pick a date to practice that call&apos;s
                  chunks.
                </p>
              </div>
              <div className="grid gap-5 sm:grid-cols-2">
                {dailyCalls.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onPick(item.id)}
                    className="btn-3d flex flex-col items-start gap-3 rounded-3xl border-[3px] border-line bg-card px-6 py-6 text-left transition hover:bg-paper-warm"
                    style={inkShadow}
                  >
                    <div className="flex w-full items-start justify-between gap-3">
                      <span className="font-display text-[20px] font-black leading-tight text-ink">
                        {item.title.replace(/^DailyCall\s*/, "")}
                      </span>
                      {item.level && (
                        <span className="shrink-0 rounded-full border-2 border-line bg-flag-gold-soft px-2.5 py-0.5 font-body text-[11px] font-black uppercase tracking-[0.1em] text-ink">
                          {item.level}
                        </span>
                      )}
                    </div>
                    {item.company && (
                      <span className="font-body text-[13px] font-semibold text-ink-muted">
                        {item.company}
                      </span>
                    )}
                    <span className="mt-1 font-body text-[12px] font-bold uppercase tracking-[0.16em] text-ink-muted">
                      {item.nChunks} {item.nChunks === 1 ? "chunk" : "chunks"}
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
