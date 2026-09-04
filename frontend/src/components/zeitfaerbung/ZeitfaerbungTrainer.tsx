"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { ZeitItem, ZeitVerdict } from "./api";
import { FeedbackCard } from "../shared/feedback";
import { playSound } from "../shared/sound";
import { InsufficientCoinsError } from "@/lib/coins";
import { OutOfCoinsPanel, refreshCoins } from "../shared/Coins";

// A missed item returns once at the end of the round — same second-chance
// contract as BauteilTrainer / VerbindungenTrainer. `retry` marks the copy.
type QueueItem = ZeitItem & { retry?: boolean };

type Phase = "intro" | "drill" | "done";

const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// The drill rules, shown once up front — same convention as the other typed
// trainers. There is no separate deep-dive explainer; these four lines are
// the only rules copy this exercise has.
const RULES: { title: string; body: string }[] = [
  {
    title: "Fill the gap with war, wurde, or blieb",
    body: "One sentence, one gap, three possible verbs. Read the meaning, not just the grammar, and type the Präteritum form that fits.",
  },
  {
    title: "Some blanks work either way",
    body: "war and wurde can both be correct in the same gap — with different meanings. When that happens the drill shows you the other reading too.",
  },
  {
    title: "Conjugate for the subject",
    body: "Match person and number: ich war, du warst, wir waren. The right verb in the wrong form still counts as a miss.",
  },
  {
    title: "wurde in disguise is often passive",
    body: "wurde followed by a past participle (wurde gefragt, wurde renoviert) is the passive — something happening TO the subject, not a state.",
  },
];

export default function ZeitfaerbungTrainer({
  round,
  onAttempt,
  onNewRound,
  flow,
  onFlowDone,
  allowGiveUp,
  onGiveUp,
  hideContinue,
}: {
  round: ZeitItem[];
  // Judge one typed verb (POST /zeitfaerbung/attempts via the parent, which
  // owns the token and the OBS-007 practice-session id).
  onAttempt: (itemId: string, answer: string) => Promise<ZeitVerdict>;
  // Fetch a fresh round; the parent remounts this component with it.
  onNewRound: () => void;
  // FLOW-001: mixed-practice mode — the parent deals exactly one item via
  // `round` and remounts per turn (via `key`), so this trainer only needs to
  // skip its own intro/round chrome and hand the verdict back instead of
  // ever reaching its own "done" phase.
  flow?: boolean;
  onFlowDone?: (correct: boolean) => void;
  // FLOW-002: the deliberate "give up" escape — Flow-only (default false),
  // so standalone practice stays pixel-identical.
  allowGiveUp?: boolean;
  onGiveUp?: (itemId: string) => Promise<ZeitVerdict>;
  // CLARA-14: Clara's room only — after a graded verdict, suppress the
  // advance button/Enter keybinding and never call onFlowDone; the parent
  // (TeacherChat) dismisses the card externally. Does NOT touch the
  // kind==="unrecognized" guidance path (no verdict is ever set there, so
  // it's untouched by this gate). Absent/false (Flow, every other caller)
  // is byte-identical.
  hideContinue?: boolean;
}) {
  const [phase, setPhase] = useState<Phase>(flow ? "drill" : "intro");
  const [queue, setQueue] = useState<QueueItem[]>(round);
  const [index, setIndex] = useState(0);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [insufficient, setInsufficient] = useState<{ needed: number; available: number } | null>(null);
  const [verdict, setVerdict] = useState<ZeitVerdict | null>(null);
  // kind === "unrecognized": guidance only, never a scored verdict. The item
  // stays put and the input stays live — this is a "try again", not a miss.
  const [guidance, setGuidance] = useState<string | null>(null);
  const [firstTryGreens, setFirstTryGreens] = useState(0);
  // First-try misses with the expected form captured from the verdict — the
  // round summary shows these to review.
  const [missed, setMissed] = useState<{ item: ZeitItem; expected: string }[]>(
    []
  );
  const inputRef = useRef<HTMLInputElement>(null);

  const item = queue[index];

  useEffect(() => {
    if (phase === "drill" && verdict === null) {
      inputRef.current?.focus();
    }
  }, [phase, index, verdict]);

  // GAME-001: "Round complete" is standalone-only — flow mode hands off via
  // onFlowDone before phase ever reaches "done" (see advance below).
  const doneSoundRef = useRef(false);
  useEffect(() => {
    if (phase === "done" && !doneSoundRef.current) {
      doneSoundRef.current = true;
      playSound("bigwin");
    }
  }, [phase]);

  const advance = useCallback(() => {
    // FLOW-001: no "done" phase in flow mode — hand the verdict to the
    // parent, which deals the next item (a fresh mount, via `key`).
    if (flow) {
      const correct = verdict?.correct ?? false;
      setVerdict(null);
      setGuidance(null);
      setValue("");
      onFlowDone?.(correct);
      return;
    }
    setVerdict(null);
    setGuidance(null);
    setValue("");
    if (index + 1 >= queue.length) {
      setPhase("done");
    } else {
      setIndex(index + 1);
    }
  }, [index, queue.length, flow, verdict, onFlowDone]);

  // Enter advances from the feedback state (the input is unmounted then).
  useEffect(() => {
    if (verdict === null || hideContinue) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        advance();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [verdict, advance, hideContinue]);

  async function check(e: React.FormEvent) {
    e.preventDefault();
    const answer = value.trim();
    if (!answer || busy) return;
    setBusy(true);
    setFailed(null);
    try {
      const res = await onAttempt(item.id, answer);
      if (res.kind === "unrecognized") {
        // Not a graded attempt — show the guidance, clear the box, stay put.
        setGuidance(res.note);
        setValue("");
        return;
      }
      setGuidance(null);
      setVerdict(res);
      // GAME-001: "Auch richtig" is a discovery, not an ordinary win — the
      // unrecognized kind above already returned before reaching a sound at
      // all (it's guidance, never a miss).
      playSound(res.correct ? (res.alt ? "bonus" : "win") : "fail");
      if (res.correct) {
        if (!item.retry) setFirstTryGreens((n) => n + 1);
      } else if (!item.retry) {
        setMissed((m) => [...m, { item, expected: res.expected }]);
        // FLOW-001: no second-chance retry re-queue in flow mode — the queue
        // stays exactly the one dealt item.
        if (!flow) setQueue((q) => [...q, { ...item, retry: true }]);
      }
    } catch (err) {
      if (err instanceof InsufficientCoinsError) {
        setInsufficient({ needed: err.needed, available: err.available });
        refreshCoins();
      } else {
        setFailed(
          err instanceof Error && err.message && !err.message.includes("failed (")
            ? err.message
            : "Couldn't check that — try again in a moment."
        );
      }
    } finally {
      setBusy(false);
    }
  }

  // FLOW-002: the deliberate give-up — grades a real miss server-side and
  // reveals the answer through the same verdict card a wrong `check()`
  // shows. `value` is overwritten so "You typed:" reads honestly instead of
  // echoing whatever partial text was sitting in the box.
  async function giveUp() {
    if (busy || !onGiveUp) return;
    setBusy(true);
    setFailed(null);
    try {
      const res = await onGiveUp(item.id);
      setGuidance(null);
      setValue("(gave up)");
      setVerdict(res);
      if (!item.retry) setMissed((m) => [...m, { item, expected: res.expected }]);
    } catch (err) {
      if (err instanceof InsufficientCoinsError) {
        setInsufficient({ needed: err.needed, available: err.available });
        refreshCoins();
      } else {
        setFailed(
          err instanceof Error && err.message && !err.message.includes("failed (")
            ? err.message
            : "Couldn't check that — try again in a moment."
        );
      }
    } finally {
      setBusy(false);
    }
  }

  if (phase === "intro") {
    return (
      <div
        className="rounded-[28px] border-[3px] border-line bg-card p-7"
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
          className="btn-3d mt-7 inline-flex items-center rounded-[20px] border-[3px] border-red-line bg-flag-red-fill px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-on-fill"
          style={redShadow}
        >
          Start · {round.length} Sätze
        </button>
      </div>
    );
  }

  if (phase === "done") {
    return (
      <div
        className="rounded-[28px] border-[3px] border-line bg-card p-7 text-center"
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
              Worth another look
            </p>
            <ul className="mt-2 space-y-1.5">
              {missed.map((m) => (
                <li
                  key={m.item.id}
                  className="font-body text-[14px] text-ink-soft"
                >
                  {m.item.frame} →{" "}
                  <span className="font-bold text-ink">{m.expected}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-7 flex items-center justify-center gap-5">
          <button
            type="button"
            onClick={onNewRound}
            className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-red-line bg-flag-red-fill px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-on-fill"
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
  // "form" (right verb, wrong conjugation) reads as amber — a smaller miss
  // than "verb" (wrong verb entirely), which stays full red.
  const tint = !solved
    ? "border-line bg-card"
    : verdict.correct
      ? "border-success bg-success-soft"
      : verdict.kind === "form"
        ? "border-flag-gold-deep bg-flag-gold-soft"
        : "border-flag-red bg-flag-red-soft";
  const gapColor = !solved
    ? ""
    : verdict.correct
      ? "text-success"
      : verdict.kind === "form"
        ? "text-flag-gold-deep"
        : "text-flag-red-deep";

  return (
    <div>
      {/* FLOW-001: the Flow page shows its own progress — hide this round's. */}
      {!flow && (
        <div className="mb-3 flex items-center justify-between">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {index + 1} / {queue.length}
            {item.retry ? " · second try" : ""}
          </p>
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {firstTryGreens} ✓
          </p>
        </div>
      )}

      <div
        className={`rounded-[28px] border-[3px] p-7 transition-colors ${tint}`}
        style={inkShadow}
      >
        {/* The frame — war? wurde? blieb? The gap doesn't say. */}
        <p className="text-center font-body text-[20px] leading-relaxed text-ink">
          {before}
          {solved ? (
            <span className={`font-bold ${gapColor}`}>{verdict.expected}</span>
          ) : (
            <span
              aria-label="gap"
              className="mx-1 inline-block min-w-[110px] border-b-[3px] border-line align-baseline"
            >
              &nbsp;
            </span>
          )}
          {after}
        </p>
        {/* Deliberately absent on ambiguous items — render nothing, not an
            empty line, when the backend omits it. */}
        {item.hint && (
          <p className="mt-2 text-center font-body text-[13px] italic text-ink-muted">
            {item.hint}
          </p>
        )}

        {!solved ? (
          <form onSubmit={check} className="mt-6">
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                ref={inputRef}
                type="text"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="Type the missing verb…"
                lang="de"
                autoCapitalize="off"
                autoComplete="off"
                autoCorrect="off"
                spellCheck={false}
                maxLength={120}
                className="min-w-0 flex-1 rounded-[18px] border-[3px] border-line bg-card px-4 py-3 font-body text-[16px] text-ink outline-none placeholder:text-ink-faint focus:border-flag-red"
              />
              <button
                type="submit"
                disabled={busy || !value.trim()}
                className="btn-3d inline-flex items-center justify-center rounded-[18px] border-[3px] border-red-line bg-flag-red-fill px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-on-fill disabled:pointer-events-none disabled:opacity-40"
                style={redShadow}
              >
                {busy ? "Checking…" : "Check"}
              </button>
            </div>
            {guidance && (
              <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-gold-deep">
                {guidance}
              </p>
            )}
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
            {/* FLOW-002: deliberately unstyled/small — never a rival to Check. */}
            {allowGiveUp && onGiveUp && (
              <p className="mt-4 text-center">
                <button
                  type="button"
                  onClick={giveUp}
                  disabled={busy}
                  className="font-body text-[11px] text-ink-muted underline-offset-2 hover:underline disabled:pointer-events-none disabled:opacity-40"
                >
                  Give up
                </button>
              </p>
            )}
          </form>
        ) : verdict.correct ? (
          <div className="mt-6">
            {verdict.alt ? (
              <>
                {/* The chosen form + what it means — the reading the learner
                    actually landed on. */}
                <p className="text-center font-body text-[15px] text-ink">
                  <span className="font-black text-success">
                    {value.trim()}
                  </span>
                  <span className="mx-1.5 text-ink-muted">—</span>
                  {verdict.reading}
                </p>
                {/* A distinct discovery card, not a correction: the other
                    valid form + the other meaning it would have carried. */}
                <div className="mt-4 rounded-[20px] border-[3px] border-flag-gold-deep bg-flag-gold-soft p-4 text-center">
                  <p className="font-body text-[11px] font-black uppercase tracking-[0.2em] text-flag-gold-deep">
                    Auch richtig
                  </p>
                  <p className="mt-1.5 font-display text-[19px] font-black text-ink">
                    {verdict.alt.form}
                  </p>
                  <p className="mt-1 font-body text-[13px] text-ink-soft">
                    {verdict.alt.reading}
                  </p>
                </div>
              </>
            ) : (
              verdict.note && (
                <p className="text-center font-body text-[14px] font-semibold leading-snug text-ink">
                  {verdict.note}
                </p>
              )
            )}
            {/* CLARA-14: Clara's room hides this — the card dismisses when
                her spoken reaction lands (or a 15s backstop), not on a
                learner click. See hideContinue's doc comment above. */}
            {!hideContinue && (
              <div className="mt-5 flex items-center justify-center gap-4">
                <button
                  type="button"
                  onClick={advance}
                  className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-line bg-card px-6 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
                  style={inkShadow}
                >
                  {flow ? "Next" : index + 1 >= queue.length ? "Finish" : "Next"}
                </button>
                <span className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                  or press Enter
                </span>
              </div>
            )}
          </div>
        ) : (
          <div className="mt-6">
            <FeedbackCard
              attempt={value.trim()}
              corrected={verdict.expected}
              note={verdict.note}
              accent={verdict.kind === "form" ? "gold" : "red"}
              patternId={verdict.patternId}
            />
            {/* CLARA-14: Clara's room hides this — the card dismisses when
                her spoken reaction lands (or a 15s backstop), not on a
                learner click. See hideContinue's doc comment above. */}
            {!hideContinue && (
              <div className="mt-5 flex items-center justify-center gap-4">
                <button
                  type="button"
                  onClick={advance}
                  className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-line bg-card px-6 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
                  style={inkShadow}
                >
                  {flow ? "Next" : index + 1 >= queue.length ? "Finish" : "Next"}
                </button>
                <span className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                  or press Enter
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
