"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { ChunkItem, ChunkVerdict } from "./api";

// A missed item returns once at the end of the round — same second-chance
// contract as BauteilTrainer. `retry` marks the copy.
type QueueItem = ChunkItem & { retry?: boolean };

type Phase = "intro" | "drill" | "done";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// The drill rules, shown once up front — same convention as BauteilTrainer.
// The wording carries the drill's whole mechanism: the gap never says whether
// a pronoun belongs, and verb + preposition + case travel as ONE word.
const RULES: { title: string; body: string }[] = [
  {
    title: "Complete the chunk",
    body: "German verbs come welded to their little words — a pronoun, a preposition, a governed case. Type exactly what the gap is missing.",
  },
  {
    title: "The gap hides the pronoun",
    body: "Some verbs need mich / dich / sich — and some famously don't. „Ich warte ___ den Bus“ takes no pronoun at all; the mix never tells you which kind you're in.",
  },
  {
    title: "One verb, one preposition, one case",
    body: "freuen auf is not freuen über — and when the article sits in the gap, its case is part of the answer. Learn verb + preposition + case as a single word.",
  },
  {
    title: "Pointing back fuses to da(r)-",
    body: "When the preposition points at a whole clause or something already said, it fuses: darauf, darüber, womit — never „auf das“.",
  },
];

export default function VerbindungenTrainer({
  round,
  onAttempt,
  onNewRound,
}: {
  round: ChunkItem[];
  // Judge one typed gap-fill (POST /verbindungen/attempts via the parent,
  // which owns the token and the OBS-007 practice-session id).
  onAttempt: (itemId: string, answer: string) => Promise<ChunkVerdict>;
  // Fetch a fresh round; the parent remounts this component with it.
  onNewRound: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("intro");
  const [queue, setQueue] = useState<QueueItem[]>(round);
  const [index, setIndex] = useState(0);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<ChunkVerdict | null>(null);
  const [firstTryGreens, setFirstTryGreens] = useState(0);
  // First-try misses with the chunk captured from the verdict — the round
  // summary shows the chunks to memorize (that's the take-away of the drill).
  const [missed, setMissed] = useState<{ item: ChunkItem; chunk: string }[]>(
    []
  );
  const inputRef = useRef<HTMLInputElement>(null);

  const item = queue[index];

  useEffect(() => {
    if (phase === "drill" && verdict === null) {
      inputRef.current?.focus();
    }
  }, [phase, index, verdict]);

  const advance = useCallback(() => {
    setVerdict(null);
    setValue("");
    if (index + 1 >= queue.length) {
      setPhase("done");
    } else {
      setIndex(index + 1);
    }
  }, [index, queue.length]);

  // Enter advances from the feedback state (the input is unmounted then).
  useEffect(() => {
    if (verdict === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        advance();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [verdict, advance]);

  async function check(e: React.FormEvent) {
    e.preventDefault();
    const answer = value.trim();
    if (!answer || busy) return;
    setBusy(true);
    setFailed(null);
    try {
      const res = await onAttempt(item.id, answer);
      setVerdict(res);
      if (res.correct) {
        if (!item.retry) setFirstTryGreens((n) => n + 1);
      } else if (!item.retry) {
        setMissed((m) => [...m, { item, chunk: res.chunk }]);
        setQueue((q) => [...q, { ...item, retry: true }]);
      }
    } catch (err) {
      setFailed(
        err instanceof Error && err.message && !err.message.includes("failed (")
          ? err.message
          : "Couldn't check that — try again in a moment."
      );
    } finally {
      setBusy(false);
    }
  }

  if (phase === "intro") {
    return (
      <div
        className="rounded-[28px] border-[3px] border-ink bg-white p-7"
        style={inkShadow}
      >
        <h2 className="font-display text-[20px] font-black tracking-tight text-ink">
          How it works
        </h2>
        <ul className="mt-5 space-y-4">
          {RULES.map((r) => (
            <li key={r.title}>
              <p className="font-body text-[12px] font-black uppercase tracking-[0.18em] text-flag-red">
                {r.title}
              </p>
              <p className="mt-1 font-body text-[14px] leading-relaxed text-ink-soft">
                {r.body}
              </p>
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={() => setPhase("drill")}
          className="btn-3d mt-7 inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-white"
          style={redShadow}
        >
          Start · {round.length} chunks
        </button>
      </div>
    );
  }

  if (phase === "done") {
    return (
      <div
        className="rounded-[28px] border-[3px] border-ink bg-white p-7 text-center"
        style={inkShadow}
      >
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          Round complete
        </p>
        <h2 className="mt-2 font-display text-[clamp(28px,5vw,40px)] font-black tracking-tight text-ink">
          {firstTryGreens} / {round.length} first try
        </h2>
        {missed.length > 0 && (
          <div className="mx-auto mt-5 max-w-[440px] text-left">
            <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              Chunks to memorize
            </p>
            <ul className="mt-2 space-y-1.5">
              {missed.map((m) => (
                <li
                  key={m.item.id}
                  className="font-body text-[14px] font-bold text-ink"
                >
                  {m.chunk}
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-7 flex items-center justify-center gap-5">
          <button
            type="button"
            onClick={onNewRound}
            className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-white"
            style={redShadow}
          >
            New round
          </button>
          <Link
            href="/practice"
            className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
          >
            ← Back to menu
          </Link>
        </div>
      </div>
    );
  }

  const [before, after] = item.frame.split("___");
  const solved = verdict !== null;
  const tint = !solved
    ? "border-ink bg-white"
    : verdict.correct
      ? "border-success bg-success-soft"
      : "border-flag-red bg-flag-red-soft";

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          {index + 1} / {queue.length}
          {item.retry ? " · second try" : ""}
        </p>
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          {firstTryGreens} ✓
        </p>
      </div>

      <div
        className={`rounded-[28px] border-[3px] p-7 transition-colors ${tint}`}
        style={inkShadow}
      >
        {/* The frame — pronoun? preposition? case? The gap doesn't say. */}
        <p className="text-center font-body text-[20px] leading-relaxed text-ink">
          {before}
          {solved ? (
            <span
              className={`font-bold ${verdict.correct ? "text-success" : "text-flag-red-deep"}`}
            >
              {verdict.expected}
            </span>
          ) : (
            <span
              aria-label="gap"
              className="mx-1 inline-block min-w-[110px] border-b-[3px] border-ink align-baseline"
            >
              &nbsp;
            </span>
          )}
          {after}
        </p>
        <p className="mt-2 text-center font-body text-[13px] italic text-ink-muted">
          {item.hint}
        </p>

        {!solved ? (
          <form onSubmit={check} className="mt-6">
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                ref={inputRef}
                type="text"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="Type the missing words…"
                lang="de"
                autoCapitalize="off"
                autoComplete="off"
                autoCorrect="off"
                spellCheck={false}
                maxLength={120}
                className="min-w-0 flex-1 rounded-[18px] border-[3px] border-ink bg-white px-4 py-3 font-body text-[16px] text-ink outline-none placeholder:text-ink-faint focus:border-flag-red"
              />
              <button
                type="submit"
                disabled={busy || !value.trim()}
                className="btn-3d inline-flex items-center justify-center rounded-[18px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-white disabled:pointer-events-none disabled:opacity-40"
                style={redShadow}
              >
                {busy ? "Checking…" : "Check"}
              </button>
            </div>
            {failed && (
              <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-red-deep">
                {failed}
              </p>
            )}
          </form>
        ) : (
          <div className="mt-6">
            <div className="flex flex-col items-center gap-2">
              {!verdict.correct && (
                <>
                  <p className="font-body text-[14px] text-ink-soft">
                    You typed:{" "}
                    <span className="font-semibold line-through decoration-flag-red decoration-2">
                      {value.trim()}
                    </span>
                  </p>
                  {verdict.note && (
                    <p className="max-w-[420px] text-center font-body text-[14px] font-semibold leading-snug text-ink">
                      {verdict.note}
                    </p>
                  )}
                </>
              )}
              {/* The canonical chunk — the actual thing to memorize, shown on
                  green AND red (the drill exists to burn these in). */}
              <p className="mt-1 rounded-full border-[2px] border-ink bg-white px-4 py-1.5 font-body text-[13px] font-black tracking-wide text-ink">
                {verdict.chunk}
              </p>
            </div>
            <div className="mt-5 flex items-center justify-center gap-4">
              <button
                type="button"
                onClick={advance}
                className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-white px-6 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
                style={inkShadow}
              >
                {index + 1 >= queue.length ? "Finish" : "Next"}
              </button>
              <span className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                or press Enter
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
