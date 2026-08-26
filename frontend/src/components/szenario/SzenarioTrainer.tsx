"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { Scenario, StructureResult } from "./api";
import GermanWay from "../shared/GermanWay";
import Glossable from "../shared/Glossable";
import { useSpeakHotkey } from "../shared/useSpeakHotkey";
import { WordRejectedError, type GlossInfo } from "../satzschmiede/api";
import { InsufficientCoinsError } from "@/lib/coins";
import { OutOfCoinsPanel, refreshCoins } from "../shared/Coins";

type Phase = "intro" | "scene" | "result";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// One spoken answer to a scene question — same recording cap as Sprechen
// (multi-sentence answers need room, but a forgotten open mic shouldn't
// stream minutes of audio).
const MAX_RECORD_SECONDS = 90;

// Chrome/Firefox record opus-in-webm, Safari aac-in-mp4 — Deepgram takes both.
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

const SENTENCE_WEIGHT_DOT: Record<"light" | "medium" | "heavy", string> = {
  light: "bg-success",
  // No flag-yellow design token exists yet — plain Tailwind amber stands in.
  medium: "bg-flag-gold",
  heavy: "bg-flag-red",
};

// Shown once, before the first question — same one-time-rules convention as
// VerbindungenTrainer / BauteilTrainer. The wording carries the whole point
// of the exercise: this is a coach toward simple German, not a grade, and
// not a grammar check.
const RULES: { title: string; body: string }[] = [
  {
    title: "One question, one shot",
    body: "Someone in the scene asks you something. Answer out loud — one recording, then it's submitted.",
  },
  {
    title: "This isn't a grade",
    body: "There's no pass or fail here — just a coach nudging you toward simpler, clearer German.",
  },
  {
    title: "Simple is easier to get right",
    body: "Short, direct sentences are easier to build correctly than long ones. Simpler German is stronger German.",
  },
  {
    title: "Watch for long, nested sentences",
    body: "The thing to avoid is stacking clause inside clause until the listener loses the thread — not using connectors.",
  },
  {
    title: "und, aber, deshalb are your friends",
    body: "Everyday connectors keep sentences light and clear. Reach for them instead of piling on relative clauses.",
  },
];

export default function SzenarioTrainer({
  scenario,
  // "scene" on every mount after the very first — the parent remounts this
  // component per question (same convention as onNewRound elsewhere), but
  // the how-it-works card is a one-time thing, not per-question.
  initialPhase,
  onStart,
  onAttempt,
  onNewQuestion,
  onGloss,
  onAdd,
  // FLOW-006: mixed-practice mode — the parent deals exactly one item via
  // `scenario` and remounts per turn (via `key`), so this trainer only
  // needs to hand the verdict back instead of ever reaching a standalone
  // "New question"/"Back to menu" navigation.
  flow,
  onFlowDone,
  // FLOW-002: the deliberate "give up" escape — Flow-only (default false),
  // so standalone practice stays pixel-identical. No audio involved, so
  // this is a separate call from `onAttempt`, not a flag on it — same shape
  // convention as SprechenTrainer's onGiveUp.
  allowGiveUp,
  onGiveUp,
  sessionId,
}: {
  scenario: Scenario;
  initialPhase: Phase;
  // Marks the parent's "has the learner started" flag so a later remount
  // (New question) skips straight to "scene".
  onStart: () => void;
  // Judge one recorded clip (POST /szenario/attempts via the parent, which
  // owns the token and the OBS-007 practice-session id).
  onAttempt: (
    scenarioId: string,
    question: string,
    audio: Blob
  ) => Promise<StructureResult>;
  // Fetch a fresh scenario; the parent remounts this component with it.
  onNewQuestion: () => void;
  // UI-007: word-gloss popover wiring — optional, absent renders plain text.
  onGloss?: (word: string, context: string) => Promise<GlossInfo>;
  // SATZ-013: resolves with the remaining gloss-path adds today (0..3) so
  // the popover can update its own counter after a successful add.
  onAdd?: (lemma: string) => Promise<{ glossRemaining?: number } | void>;
  flow?: boolean;
  onFlowDone?: (correct: boolean) => void;
  allowGiveUp?: boolean;
  onGiveUp?: (
    scenarioId: string,
    question: string
  ) => Promise<StructureResult>;
  // OBS-007 practice-sitting id, owned by the parent (Szenario.tsx or
  // Flow.tsx) — threaded through to GermanWay's rephrase call.
  sessionId?: string;
}) {
  // FLOW-006: force "scene" in flow mode regardless of what `initialPhase`
  // the parent passed — the one-time "How it works" card belongs to the
  // standalone page's first visit only, never to a mixed-practice turn.
  const [phase, setPhase] = useState<Phase>(flow ? "scene" : initialPhase);
  const [recording, setRecording] = useState(false);
  const [checking, setChecking] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [verdict, setVerdict] = useState<StructureResult | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [insufficient, setInsufficient] = useState<{ needed: number; available: number } | null>(null);
  // SZEN-002: transcript / sentence weights / skeleton hide behind Details.
  const [showDetails, setShowDetails] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  // Set when the clip must NOT be submitted (unmount mid-recording).
  const discardRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopRecording = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
    }
    setRecording(false);
  }, []);

  // UI-006: a messed-up take — kill the clip without submitting; the next
  // startRecording resets discardRef, so the learner just records again.
  const discardRecording = useCallback(() => {
    discardRef.current = true;
    stopRecording();
  }, [stopRecording]);

  // Hard cap — a forgotten mic auto-submits at the limit rather than growing.
  useEffect(() => {
    if (recording && elapsed >= MAX_RECORD_SECONDS) {
      stopRecording();
    }
  }, [recording, elapsed, stopRecording]);

  // Unmount mid-recording: kill the mic, never submit the partial clip.
  useEffect(() => {
    return () => {
      discardRef.current = true;
      if (timerRef.current) clearInterval(timerRef.current);
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
    };
  }, []);

  // UI-012: Space/Enter mirror the mouse — toggle Record while a question is
  // live ("scene"), advance via the primary continue once a verdict is
  // showing ("result") — `next()` below already branches on `flow`. "intro"
  // (the one-time how-it-works card, with its own "Start" button) is
  // deliberately left alone — not a record control or a verdict continue.
  // The Record button here has no `disabled` prop — it simply isn't
  // rendered while `checking` — so that's the only extra guard.
  useSpeakHotkey(() => {
    if (phase === "result") {
      next();
      return;
    }
    if (phase !== "scene" || checking) return;
    if (recording) {
      stopRecording();
    } else {
      void startRecording();
    }
  });

  async function startRecording() {
    setFailed(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setFailed("We need the microphone for this one — check the browser permission.");
      return;
    }
    // MediaRecorder construction can throw on older Safari / restrictive
    // WebViews — outside a try/catch that leaves the mic stream we just
    // acquired orphaned (hot, unreachable by any cleanup).
    try {
      const mime = MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m));
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunksRef.current = [];
      discardRef.current = false;
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        if (discardRef.current) return;
        const blob = new Blob(chunksRef.current, { type: rec.mimeType });
        void submit(blob);
      };
      recorderRef.current = rec;
      rec.start();
      setRecording(true);
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      setFailed("We need the microphone for this one — check the browser permission.");
    }
  }

  async function submit(audio: Blob) {
    setChecking(true);
    try {
      const res = await onAttempt(scenario.scenarioId, scenario.question, audio);
      setVerdict(res);
      setPhase("result");
    } catch (err) {
      if (err instanceof InsufficientCoinsError) {
        setInsufficient({ needed: err.needed, available: err.available });
        refreshCoins();
      } else if (err instanceof WordRejectedError) {
        // Learner-facing message from the backend ("We couldn't hear
        // anything…") — stay on "scene" so the learner can re-record.
        setFailed(err.message);
      } else {
        setFailed(
          err instanceof Error && err.message && !err.message.includes("failed (")
            ? err.message
            : "Couldn't check that — try again in a moment."
        );
      }
    } finally {
      setChecking(false);
    }
  }

  // FLOW-002: the deliberate give-up — no mic involved, so this hits its own
  // handler instead of `submit`, but lands in the same `verdict`/"result"
  // state a real attempt would, so `next()` below needs no change.
  async function giveUp() {
    if (checking || recording || !onGiveUp) return;
    setChecking(true);
    setFailed(null);
    try {
      const res = await onGiveUp(scenario.scenarioId, scenario.question);
      setVerdict(res);
      setPhase("result");
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
      setChecking(false);
    }
  }

  // FLOW-006: no "New question"/"Back to menu" in flow mode — hand the
  // verdict to the parent, which deals the next item (a fresh mount, via
  // `key`). FLOW-006's tally rule: `clear`/`a_bit_heavy` count as correct,
  // `overcomplicated` doesn't — a give-up forces `overcomplicated` (via
  // `gaveUp`) regardless of whatever placeholder verdict it carries.
  function next() {
    if (flow) {
      const correct = !!verdict && verdict.verdict !== "overcomplicated" && !verdict.gaveUp;
      onFlowDone?.(correct);
      return;
    }
    onNewQuestion();
  }

  // Same-question redo: unlike New question (a fresh scenario), this reuses
  // `scenario` as-is — clear the verdict/details and drop back to "scene" so
  // the mic UI is live, then start recording immediately. GermanWay isn't
  // reset here directly: it only renders inside the "result" block, so
  // flipping the phase unmounts it along with the rest of the verdict.
  function retry() {
    setVerdict(null);
    setShowDetails(false);
    setPhase("scene");
    void startRecording();
  }

  if (phase === "intro") {
    return (
      <div
        className="rounded-[28px] border-[3px] border-ink bg-card p-7"
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
          onClick={() => {
            onStart();
            setPhase("scene");
          }}
          className="btn-3d mt-7 inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-on-fill"
          style={redShadow}
        >
          Start
        </button>
      </div>
    );
  }

  if (phase === "result" && verdict?.gaveUp) {
    // FLOW-002: a modest feedback state — there was no recording to judge,
    // so the full badge/skeleton breakdown below (built for a real attempt)
    // would just show empty/blank. `gaveUp` is only ever set by the Flow's
    // give-up handler, so `next()` always takes the flow branch here.
    return (
      <div>
        <div className="mb-3 flex items-center justify-between">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {scenario.persona.name} asked
          </p>
        </div>
        <div
          className="rounded-[28px] border-[3px] border-ink bg-card p-7 text-center"
          style={inkShadow}
        >
          <p className="font-body text-[14px] font-semibold text-flag-red-deep">
            {verdict.coachMessage}
          </p>
          <div className="mt-6 flex items-center justify-center">
            <button
              type="button"
              onClick={next}
              className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-card px-6 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
              style={inkShadow}
            >
              Next
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (phase === "result" && verdict) {
    // Soft tint for the verdict block — encouraging either way, never
    // clinical pass/fail. Only "overcomplicated" leans on the (soft) red;
    // a_bit_heavy sits between on amber (no flag-yellow token exists yet).
    const coachTint =
      verdict.verdict === "overcomplicated"
        ? "border-flag-red bg-flag-red-soft"
        : verdict.verdict === "a_bit_heavy"
          ? "border-flag-gold bg-flag-gold-soft"
          : "border-success bg-success-soft";
    const VERDICT_BADGE: Record<
      StructureResult["verdict"],
      { label: string; cls: string }
    > = {
      clear: { label: "✓ Clear", cls: "border-success bg-success text-on-fill" },
      a_bit_heavy: {
        label: "A bit heavy",
        cls: "border-flag-gold-deep bg-flag-gold text-ink-fixed",
      },
      overcomplicated: {
        label: "Overcomplicated",
        cls: "border-flag-red-deep bg-flag-red text-on-fill",
      },
    };
    const badge = VERDICT_BADGE[verdict.verdict];
    // The ONE instant takeaway when something was heavy: the best lighter
    // rebuild — a heavy sentence's if any, else the first one offered.
    const bestSimpler =
      verdict.sentences.find((s) => s.weight === "heavy" && s.simpler) ??
      verdict.sentences.find((s) => s.simpler);

    return (
      <div>
        <div className="mb-3 flex items-center justify-between">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {scenario.persona.name} asked
          </p>
        </div>

        <div
          className="rounded-[28px] border-[3px] border-ink bg-card p-7"
          style={inkShadow}
        >
          {/* SZEN-002: the instant verdict IS the screen — badge, coach
              message, and the single best lighter rebuild. Everything else
              waits behind Details. */}
          <div
            className={`rounded-[18px] border-[3px] px-5 py-5 text-center ${coachTint}`}
          >
            <div className="flex flex-wrap items-center justify-center gap-2">
              <span
                className={`inline-flex items-center rounded-full border-[2px] px-4 py-1.5 font-display text-[13px] font-black uppercase tracking-[0.14em] ${badge.cls}`}
              >
                {badge.label}
              </span>
              <span className="inline-flex items-center rounded-full border-[2px] border-ink bg-card px-3 py-1 font-body text-[10px] font-black uppercase tracking-[0.18em] text-ink-muted">
                {verdict.levelRead}
              </span>
            </div>
            <p className="mt-3 font-display text-[19px] font-black leading-snug text-ink">
              {verdict.coachMessage}
            </p>
            {bestSimpler?.simpler && (
              <div className="mt-4 rounded-[14px] border-[2px] border-ink/20 bg-card px-4 py-3 text-left">
                <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                  Try it lighter
                </p>
                <p className="mt-1 font-body text-[16px] font-bold leading-snug text-ink">
                  {bestSimpler.simpler}
                </p>
              </div>
            )}
          </div>

          {/* IDIOM-002 P1: on-demand phrasing advice on the whole answer —
              separate from the structure verdict above. */}
          {verdict.transcript.trim() !== "" && (
            <GermanWay
              text={verdict.transcript}
              context={scenario.question}
              onGloss={onGloss}
              onAdd={onAdd}
              sessionId={sessionId}
            />
          )}

          {/* Details on demand: what we heard, per-sentence weights, skeleton. */}
          <div className="mt-5 text-center">
            <button
              type="button"
              onClick={() => setShowDetails((v) => !v)}
              className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-card px-5 py-2 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink"
              style={inkShadow}
            >
              {showDetails ? "Hide details ▴" : "Details ▾"}
            </button>
          </div>

          {showDetails && (
            <>
              {/* The raw transcript IS part of the exercise — what you actually
                  said, not what you meant to say. */}
              <div className="mt-4 rounded-[18px] border-[3px] border-ink bg-card px-4 py-3">
                <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                  What we heard
                </p>
                <p className="mt-1 font-body text-[15px] leading-relaxed text-ink">
                  {verdict.transcript}
                </p>
              </div>

              {/* One row per sentence, colored by how heavy it felt to carry —
                  this is a complexity read, not a correctness check. A non-null
                  `simpler` offers a lighter way to say the same thing. */}
              {verdict.sentences.length > 0 && (
                <div className="mt-6">
                  <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                    How heavy each sentence felt
                  </p>
                  <ul className="mt-2 space-y-2.5">
                    {verdict.sentences.map((s, i) => (
                      <li
                        key={i}
                        className="rounded-[16px] border-[3px] border-ink bg-card px-4 py-3"
                      >
                        <div className="flex items-start gap-2.5">
                          <span
                            aria-hidden
                            className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${SENTENCE_WEIGHT_DOT[s.weight]}`}
                          />
                          <p className="font-body text-[15px] leading-relaxed text-ink">
                            {s.text}
                          </p>
                        </div>
                        {s.simpler && (
                          <p className="mt-1.5 pl-5 font-body text-[12px] font-semibold text-ink-muted">
                            lighter: {s.simpler}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Skeleton — the extracted shape of the answer: the core claim,
                  the supporting points, where it jumped off track, and the
                  vocabulary it anchored on. */}
              <div className="mt-6 rounded-[18px] border-[3px] border-ink bg-card px-4 py-4">
                <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                  Skeleton
                </p>

                <p className="mt-3 font-body text-[10px] font-black uppercase tracking-[0.18em] text-flag-red">
                  Kern
                </p>
                <p className="mt-1 font-body text-[15px] font-bold leading-relaxed text-ink">
                  {verdict.skeleton.kern}
                </p>

                {verdict.skeleton.punkte.length > 0 && (
                  <>
                    <p className="mt-3 font-body text-[10px] font-black uppercase tracking-[0.18em] text-flag-red">
                      Punkte
                    </p>
                    <ul className="mt-1 list-disc space-y-1 pl-5">
                      {verdict.skeleton.punkte.map((p, i) => (
                        <li key={i} className="font-body text-[14px] text-ink-soft">
                          {p}
                        </li>
                      ))}
                    </ul>
                  </>
                )}

                {verdict.skeleton.absprung && (
                  <>
                    <p className="mt-3 font-body text-[10px] font-black uppercase tracking-[0.18em] text-flag-red">
                      Absprung
                    </p>
                    <p className="mt-1 font-body text-[14px] italic leading-relaxed text-ink-soft">
                      {verdict.skeleton.absprung}
                    </p>
                  </>
                )}

                {verdict.skeleton.vokabelAnker.length > 0 && (
                  <>
                    <p className="mt-3 font-body text-[10px] font-black uppercase tracking-[0.18em] text-flag-red">
                      Vokabel-Anker
                    </p>
                    <div className="mt-1.5 flex flex-wrap gap-2">
                      {verdict.skeleton.vokabelAnker.map((w) => (
                        <span
                          key={w}
                          className="rounded-full border-[2px] border-ink bg-card px-4 py-1.5 font-body text-[13px] font-black tracking-wide text-ink"
                        >
                          {w}
                        </span>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </>
          )}

          <div className="mt-7 flex items-center justify-center gap-5">
            <button
              type="button"
              onClick={retry}
              className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red"
            >
              ↻ Try again
            </button>
            {/* FLOW-006: in flow mode this is the single continue action —
                onNewQuestion (a fresh scenario fetch) is never called; `next`
                hands the verdict to the parent instead. Standalone keeps its
                own "New question" + "Back to menu" exactly as before. */}
            <button
              type="button"
              onClick={next}
              className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-on-fill"
              style={redShadow}
            >
              {flow ? "Next" : "New question"}
            </button>
            {!flow && (
              <Link
                href="/practice"
                className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
              >
                ← Back to menu
              </Link>
            )}
          </div>
        </div>
      </div>
    );
  }

  // phase === "scene"
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          {scenario.persona.name} · {scenario.persona.role}
        </p>
      </div>

      <div
        className="rounded-[28px] border-[3px] border-ink bg-card p-7"
        style={inkShadow}
      >
        <p className="mx-auto max-w-[480px] text-center font-body text-[14px] italic leading-relaxed text-ink-muted">
          {scenario.kontext}
        </p>
        <p className="mx-auto mt-4 max-w-[480px] text-center font-body text-[20px] font-bold leading-relaxed text-ink">
          {onGloss ? (
            <Glossable text={scenario.question} onGloss={onGloss} onAdd={onAdd} />
          ) : (
            scenario.question
          )}
        </p>

        <div className="mt-7 flex flex-col items-center gap-3">
          {checking ? (
            <p className="font-body text-[14px] font-semibold text-ink-muted">
              Checking…
            </p>
          ) : (
            <>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={recording ? stopRecording : startRecording}
                  className={`btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] px-7 py-3.5 font-display text-[14px] font-black uppercase tracking-[0.16em] ${
                    recording
                      ? "animate-pulse border-flag-red-deep bg-card text-flag-red"
                      : "border-flag-red-deep bg-flag-red text-on-fill"
                  }`}
                  style={redShadow}
                >
                  {recording ? `Stop · ${elapsed}s` : "Record"}
                </button>
                {/* UI-006: visible only mid-recording — the escape hatch for
                    a take you don't want judged. */}
                {recording && (
                  <button
                    type="button"
                    onClick={discardRecording}
                    aria-label="Discard recording"
                    title="Discard recording"
                    className="btn-3d inline-flex h-[52px] w-[52px] items-center justify-center rounded-[20px] border-[3px] border-ink bg-card font-display text-[18px] font-black text-ink"
                    style={inkShadow}
                  >
                    ✕
                  </button>
                )}
              </div>
              <p className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                {recording
                  ? "speak, then tap stop — ✕ discards"
                  : "tap record — answer in German, short and simple"}
                {/* UI-012: desktop-only Space hint — hidden on touch (no
                    hover) devices, where there's no keyboard to hint at. */}
                <span className="hidden [@media(hover:hover)]:inline">
                  {" "}
                  · Space
                </span>
              </p>
              {/* FLOW-002: deliberately unstyled/small — never a rival to Record. */}
              {allowGiveUp && onGiveUp && (
                <button
                  type="button"
                  onClick={giveUp}
                  disabled={recording}
                  className="font-body text-[11px] text-ink-muted underline-offset-2 hover:underline disabled:pointer-events-none disabled:opacity-40"
                >
                  Give up
                </button>
              )}
            </>
          )}
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
        </div>
      </div>
    </div>
  );
}
