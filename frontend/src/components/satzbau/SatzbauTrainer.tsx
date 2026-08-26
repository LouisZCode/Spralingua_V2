"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { ClauseItem, ClauseVerdict } from "./api";
import Glossable from "../shared/Glossable";
import type { GlossInfo } from "../satzschmiede/api";
import { FeedbackCard } from "../shared/feedback";
import { playSound } from "../shared/sound";
import { InsufficientCoinsError } from "@/lib/coins";
import { OutOfCoinsPanel, refreshCoins } from "../shared/Coins";

// A missed item returns once at the end of the round — same second-chance
// contract as the sibling drills. `retry` marks the copy.
type QueueItem = ClauseItem & { retry?: boolean };

type Phase = "intro" | "drill" | "done";

const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// The drill rules, shown once up front — same convention as the sibling
// trainers.
const RULES: { title: string; body: string }[] = [
  {
    title: "Tap to build",
    body: "You get a lead-in (if there is one) and a row of word chips. Tap a chip to place it in order; tap a placed chip to send it back.",
  },
  {
    title: "The task carries the meaning",
    body: "Read it before you start placing chips — German word order depends on what you're actually saying, not just which words you were handed.",
  },
  {
    title: "The verb has a fixed home",
    body: "Subordinate clauses and indirect questions send the conjugated verb to the very end. Direct questions put it first (yes/no) or right after the question word.",
  },
  {
    title: "More than one order can be right",
    body: "German is flexible inside a clause — a different-but-valid order still counts as correct, and we'll tell you when your version and ours simply read differently.",
  },
];

export default function SatzbauTrainer({
  round,
  onAttempt,
  onNewRound,
  onGloss,
  onAdd,
  flow,
  onFlowDone,
  allowGiveUp,
  onGiveUp,
}: {
  round: ClauseItem[];
  // Judge one built chip order (POST /satzbau/attempts via the parent,
  // which owns the token and the OBS-007 practice-session id).
  onAttempt: (itemId: string, order: string[]) => Promise<ClauseVerdict>;
  // Fetch a fresh round; the parent remounts this component with it.
  onNewRound: () => void;
  // UI-007: word-gloss popover wiring — optional, absent renders plain text.
  onGloss?: (word: string, context: string) => Promise<GlossInfo>;
  // SATZ-013: resolves with the remaining gloss-path adds today (0..3) so
  // the popover can update its own counter after a successful add.
  onAdd?: (lemma: string) => Promise<{ glossRemaining?: number } | void>;
  // FLOW-001: mixed-practice mode — the parent deals exactly one item via
  // `round` and remounts per turn (via `key`), so this trainer only needs
  // to skip its own intro/round chrome and hand the verdict back instead
  // of ever reaching its own "done" phase.
  flow?: boolean;
  onFlowDone?: (correct: boolean) => void;
  // FLOW-002: the deliberate "give up" escape — Flow-only (default false),
  // so standalone practice stays pixel-identical.
  allowGiveUp?: boolean;
  onGiveUp?: (itemId: string) => Promise<ClauseVerdict>;
}) {
  const [phase, setPhase] = useState<Phase>(flow ? "drill" : "intro");
  const [queue, setQueue] = useState<QueueItem[]>(round);
  const [index, setIndex] = useState(0);
  // Chip identity is by INDEX into item.chips, not by string — two chips
  // can share the same surface text (unlikely in the current catalog, but
  // nothing here should rely on tokens being unique).
  const [trayIdx, setTrayIdx] = useState<number[]>([]);
  const [placedIdx, setPlacedIdx] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [insufficient, setInsufficient] = useState<{ needed: number; available: number } | null>(null);
  const [verdict, setVerdict] = useState<ClauseVerdict | null>(null);
  const [gaveUp, setGaveUp] = useState(false);
  // The hint points at the decision (never the answer) but can still prime
  // L1 interference, so it stays hidden until asked — grading always
  // reveals it (same "Show English" contract as the sibling trainers).
  const [hintShown, setHintShown] = useState(false);
  const [firstTryGreens, setFirstTryGreens] = useState(0);
  // First-try misses with the rule captured from the verdict — the round
  // summary shows the rules to memorize (that's the take-away of the drill).
  const [missed, setMissed] = useState<{ item: ClauseItem; rule: string }[]>(
    []
  );

  const item = queue[index];

  // Reset the tray/placed state whenever the dealt item changes. In
  // standalone mode `index` advances without remounting the component; in
  // flow mode the parent remounts via `key` per deal, so this also covers
  // that case on first mount.
  useEffect(() => {
    if (item) {
      setTrayIdx(item.chips.map((_, i) => i));
      setPlacedIdx([]);
      setGaveUp(false);
    }
  }, [item]);

  // GAME-001: "Round complete" is standalone-only — flow mode hands off via
  // onFlowDone before phase ever reaches "done" (see advance below).
  const doneSoundRef = useRef(false);
  useEffect(() => {
    if (phase === "done" && !doneSoundRef.current) {
      doneSoundRef.current = true;
      playSound("bigwin");
    }
  }, [phase]);

  const solved = verdict !== null;

  const placeChip = useCallback(
    (chipI: number) => {
      if (solved || busy) return;
      setTrayIdx((t) => t.filter((i) => i !== chipI));
      setPlacedIdx((p) => [...p, chipI]);
    },
    [solved, busy]
  );

  const removeChip = useCallback(
    (pos: number) => {
      if (solved || busy) return;
      const chipI = placedIdx[pos];
      setPlacedIdx((p) => p.filter((_, i) => i !== pos));
      setTrayIdx((t) => [...t, chipI]);
    },
    [solved, busy, placedIdx]
  );

  const advance = useCallback(() => {
    // FLOW-001: no "done" phase in flow mode — hand the verdict to the
    // parent, which deals the next item (a fresh mount, via `key`).
    if (flow) {
      const correct = verdict?.correct ?? false;
      setVerdict(null);
      onFlowDone?.(correct);
      return;
    }
    setVerdict(null);
    setHintShown(false);
    if (index + 1 >= queue.length) {
      setPhase("done");
    } else {
      setIndex(index + 1);
    }
  }, [index, queue.length, flow, verdict, onFlowDone]);

  // Enter advances from the feedback state.
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

  async function check() {
    if (busy || trayIdx.length > 0) return;
    const order = placedIdx.map((i) => item.chips[i]);
    setBusy(true);
    setFailed(null);
    try {
      const res = await onAttempt(item.id, order);
      setVerdict(res);
      playSound(res.correct ? "win" : "fail");
      if (res.correct) {
        if (!item.retry) setFirstTryGreens((n) => n + 1);
      } else if (!item.retry) {
        setMissed((m) => [...m, { item, rule: res.rule }]);
        // FLOW-001: no second-chance retry re-queue in flow mode — the
        // queue stays exactly the one dealt item.
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
  // reveals the order through the same verdict card a wrong check() shows.
  async function giveUp() {
    if (busy || !onGiveUp) return;
    setBusy(true);
    setFailed(null);
    try {
      const res = await onGiveUp(item.id);
      setGaveUp(true);
      setVerdict(res);
      if (!item.retry) setMissed((m) => [...m, { item, rule: res.rule }]);
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
              Rules to memorize
            </p>
            <ul className="mt-2 space-y-1.5">
              {missed.map((m) => (
                <li
                  key={m.item.id}
                  className="font-body text-[13px] font-semibold leading-snug text-ink"
                >
                  {m.rule}
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

  const tint = !solved
    ? "border-line bg-card"
    : verdict.correct
      ? "border-success bg-success-soft"
      : "border-flag-red bg-flag-red-soft";

  const builtText = gaveUp
    ? "(gave up)"
    : [item.given, ...placedIdx.map((i) => item.chips[i])]
        .filter(Boolean)
        .join(" ");
  const correctedText = [item.given, ...(verdict?.expected ?? [])]
    .filter(Boolean)
    .join(" ");

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
        {/* The task — the meaning to build. Always visible: a chip drill
            with no stated target is just a jigsaw. */}
        <p className="text-center font-body text-[13px] font-black uppercase tracking-[0.18em] text-flag-red">
          Build it
        </p>
        <p className="mt-1.5 text-center font-body text-[15px] font-semibold italic leading-relaxed text-ink-soft">
          {item.task}
        </p>

        {!solved ? (
          <>
            {/* Build line — the given lead-in renders fixed and unmovable;
                placed chips are tappable to send back to the tray. UI-007:
                the lead-in is glossable (it's German prose the learner
                reads), but chips below are deliberately NOT — a chip's tap
                already has a job (place/remove it), and hijacking that tap
                for a gloss popover would break the drill. */}
            <div className="mt-5 flex min-h-[72px] flex-wrap items-center justify-center gap-2 rounded-[18px] border-[3px] border-dashed border-ink-faint bg-paper-warm p-4">
              {item.given && (
                <span className="font-body text-[18px] font-bold leading-relaxed text-ink">
                  {onGloss ? (
                    <Glossable text={item.given} onGloss={onGloss} onAdd={onAdd} />
                  ) : (
                    item.given
                  )}
                </span>
              )}
              {placedIdx.length === 0 && !item.given && (
                <span className="font-body text-[13px] font-semibold text-ink-faint">
                  Tap chips below to build the sentence
                </span>
              )}
              {placedIdx.map((chipI, pos) => (
                <button
                  key={chipI}
                  type="button"
                  onClick={() => removeChip(pos)}
                  className="btn-3d rounded-[14px] border-[3px] border-red-line bg-flag-red-fill px-3.5 py-1.5 font-display text-[16px] font-black text-on-fill"
                >
                  {item.chips[chipI]}
                </button>
              ))}
            </div>

            {/* Tray — the remaining chips, tap to place. */}
            <div className="mt-4 flex min-h-[52px] flex-wrap items-center justify-center gap-2">
              {trayIdx.map((chipI) => (
                <button
                  key={chipI}
                  type="button"
                  onClick={() => placeChip(chipI)}
                  className="btn-3d rounded-[14px] border-[3px] border-line bg-card px-3.5 py-1.5 font-display text-[16px] font-black text-ink hover:bg-paper-warm"
                >
                  {item.chips[chipI]}
                </button>
              ))}
            </div>
          </>
        ) : (
          // The chips are gone once solved — this is now plain text (right
          // or wrong), so UI-007 glosses it same as the lead-in above. The
          // give-up placeholder is English, not German: no gloss on that one.
          <p className="mt-5 text-center font-body text-[19px] font-bold leading-relaxed text-ink">
            <span className={verdict.correct ? "text-success" : "text-flag-red-deep"}>
              {onGloss && !gaveUp ? (
                <Glossable text={builtText} onGloss={onGloss} onAdd={onAdd} />
              ) : (
                builtText
              )}
            </span>
          </p>
        )}

        {solved || hintShown ? (
          <p className="mt-3 text-center font-body text-[13px] italic text-ink-muted">
            {item.hint}
          </p>
        ) : (
          <p className="mt-3 text-center">
            <button
              type="button"
              onClick={() => setHintShown(true)}
              className="font-body text-[11px] font-bold uppercase tracking-[0.2em] text-ink-faint underline-offset-2 hover:text-ink-muted hover:underline"
            >
              Show hint
            </button>
          </p>
        )}

        {!solved ? (
          <div className="mt-6 flex flex-col items-center gap-3">
            <button
              type="button"
              onClick={check}
              disabled={busy || trayIdx.length > 0}
              className="btn-3d inline-flex items-center justify-center rounded-[18px] border-[3px] border-red-line bg-flag-red-fill px-7 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-on-fill disabled:pointer-events-none disabled:opacity-40"
              style={redShadow}
            >
              {busy ? "Checking…" : "Check"}
            </button>
            {insufficient && (
              <div className="mt-3">
                <OutOfCoinsPanel needed={insufficient.needed} available={insufficient.available} onDismiss={() => setInsufficient(null)} />
              </div>
            )}
            {failed && (
              <p className="text-center font-body text-[13px] font-semibold text-flag-red-deep">
                {failed}
              </p>
            )}
            {/* FLOW-002: deliberately unstyled/small — never a rival to Check. */}
            {allowGiveUp && onGiveUp && (
              <button
                type="button"
                onClick={giveUp}
                disabled={busy}
                className="font-body text-[11px] text-ink-muted underline-offset-2 hover:underline disabled:pointer-events-none disabled:opacity-40"
              >
                Give up
              </button>
            )}
          </div>
        ) : (
          <div className="mt-6">
            {!verdict.correct && (
              // Word-level diff (never strikethrough — wrong tokens turn
              // red, the correction turns green): the same shared skeleton
              // every drill uses (shared/feedback.tsx).
              <FeedbackCard
                attempt={builtText}
                corrected={correctedText}
                note={verdict.note}
              />
            )}
            {/* The variant — a genuinely different-but-valid order the
                learner built. Framed as information, never as an error. */}
            {verdict.correct && verdict.variant && (
              <div className="mx-auto mt-1 max-w-[440px] rounded-[18px] border-[3px] border-flag-gold-deep bg-flag-gold-soft p-4 text-left">
                <p className="font-body text-[11px] font-black uppercase tracking-[0.2em] text-flag-gold-deep">
                  Also fine — how it reads
                </p>
                <p className="mt-1.5 font-body text-[13px] leading-snug text-ink">
                  {verdict.variant}
                </p>
              </div>
            )}
            {/* The rule — the actual thing to memorize, shown on green AND
                red (the drill exists to burn these in). */}
            <p className="mt-3 text-center font-body text-[13px] font-black leading-snug tracking-wide text-ink">
              {verdict.rule}
            </p>
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
          </div>
        )}
      </div>
    </div>
  );
}
