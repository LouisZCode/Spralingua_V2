"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { NudgeWord, SpokenTask, SprechenVerdict } from "./api";
import GermanWay from "../shared/GermanWay";
import Glossable from "../shared/Glossable";
import VocabNudgePill from "../shared/VocabNudge";
import { useSpeakHotkey } from "../shared/useSpeakHotkey";
import type { GlossInfo } from "../satzschmiede/api";
import { diffWords, segmentTranscript } from "./slipDiff";
import { playSound } from "../shared/sound";

type Phase = "drill" | "done";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// Multi-sentence answers need more room than Satzschmiede's one-sentence cap,
// but a forgotten open mic still shouldn't stream minutes of audio.
const MAX_RECORD_SECONDS = 90;

// Chrome/Firefox record opus-in-webm, Safari aac-in-mp4 — Deepgram takes both.
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

export default function SprechenTrainer({
  round,
  onAttempt,
  onNewRound,
  onGloss,
  onAdd,
  onNudge,
  flow,
  onFlowDone,
  allowGiveUp,
  onGiveUp,
  sessionId,
}: {
  round: SpokenTask[];
  // Judge one recorded clip (POST /sprechen/attempts via the parent, which
  // owns the token and the OBS-007 practice-session id).
  onAttempt: (taskId: string, audio: Blob) => Promise<SprechenVerdict>;
  // Fetch a fresh round; the parent remounts this component with it.
  onNewRound: () => void;
  // UI-007: word-gloss popover wiring — optional, absent renders plain text.
  onGloss?: (word: string, context: string) => Promise<GlossInfo>;
  // SATZ-013: resolves with the remaining gloss-path adds today (0..3) so
  // the popover can update its own counter after a successful add.
  onAdd?: (lemma: string) => Promise<{ glossRemaining?: number } | void>;
  // The vocab nudge (POST /sprechen/nudge): which of the learner's own deck
  // words would fit an answer to this task. Optional — Flow never passes it,
  // so Flow stays nudge-free by omission (no flow-gating needed here, unlike
  // Genus's drag/production split).
  onNudge?: (taskId: string) => Promise<NudgeWord[]>;
  // FLOW-001: mixed-practice mode — the parent deals exactly one item via
  // `round` and remounts per turn (via `key`), so this trainer only needs to
  // hand the verdict back instead of ever reaching its own "done" phase.
  // Sprechen already starts at "drill" (no intro), so no phase-init change.
  flow?: boolean;
  onFlowDone?: (correct: boolean) => void;
  // FLOW-002: the deliberate "give up" escape — Flow-only (default false),
  // so standalone practice stays pixel-identical. No audio involved, so
  // this is a separate call from `onAttempt`, not a flag on it.
  allowGiveUp?: boolean;
  onGiveUp?: (taskId: string) => Promise<SprechenVerdict>;
  // OBS-007 practice-sitting id, owned by the parent (Sprechen.tsx or
  // Flow.tsx) — threaded through to GermanWay's rephrase call.
  sessionId?: string;
}) {
  const [phase, setPhase] = useState<Phase>("drill");
  const [index, setIndex] = useState(0);
  const [recording, setRecording] = useState(false);
  const [checking, setChecking] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [verdict, setVerdict] = useState<SprechenVerdict | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  // Latest verdict per task (a retry replaces it) — the summary counts these.
  const [results, setResults] = useState<(SprechenVerdict | null)[]>(() =>
    round.map(() => null)
  );

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  // Set when the clip must NOT be submitted (unmount mid-recording).
  const discardRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const task = round[index];

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

  // GAME-001: "Round complete" is standalone-only — flow mode hands off via
  // onFlowDone before phase ever reaches "done" (see next() below).
  const doneSoundRef = useRef(false);
  useEffect(() => {
    if (phase === "done" && !doneSoundRef.current) {
      doneSoundRef.current = true;
      playSound("bigwin");
    }
  }, [phase]);

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

  // UI-012: Space/Enter mirror the mouse — toggle Record while the task is
  // still open, advance via the primary Next/Finish once a verdict (real or
  // given-up) is showing. The Record button here has no `disabled` prop at
  // all — it simply isn't rendered while `checking` — so the only guard
  // needed is not calling into it during that window.
  useSpeakHotkey(() => {
    if (verdict !== null) {
      next();
      return;
    }
    if (checking) return;
    if (recording) {
      stopRecording();
    } else {
      void startRecording();
    }
  });

  async function startRecording() {
    setFailed(null);
    setVerdict(null);
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
      const res = await onAttempt(task.id, audio);
      setVerdict(res);
      playSound(res.passed ? "win" : "fail");
      setResults((r) => {
        const next = [...r];
        next[index] = res;
        return next;
      });
    } catch (err) {
      setFailed(
        err instanceof Error && err.message && !err.message.includes("failed (")
          ? err.message
          : "Couldn't check that — try again in a moment."
      );
    } finally {
      setChecking(false);
    }
  }

  // FLOW-002: the deliberate give-up — no mic involved, so this hits its own
  // endpoint instead of `submit`, but lands in the same `verdict`/`results`
  // state a real (failed) attempt would, so `next()` below needs no change.
  async function giveUp() {
    if (checking || recording || !onGiveUp) return;
    setChecking(true);
    setFailed(null);
    try {
      const res = await onGiveUp(task.id);
      setVerdict(res);
      setResults((r) => {
        const next = [...r];
        next[index] = res;
        return next;
      });
    } catch (err) {
      setFailed(
        err instanceof Error && err.message && !err.message.includes("failed (")
          ? err.message
          : "Couldn't check that — try again in a moment."
      );
    } finally {
      setChecking(false);
    }
  }

  function next() {
    // FLOW-001: no "done" phase in flow mode — hand the verdict to the
    // parent, which deals the next item (a fresh mount, via `key`).
    if (flow) {
      const passed = verdict?.passed ?? false;
      setVerdict(null);
      setFailed(null);
      onFlowDone?.(passed);
      return;
    }
    setVerdict(null);
    setFailed(null);
    if (index + 1 >= round.length) {
      setPhase("done");
    } else {
      setIndex(index + 1);
    }
  }

  if (phase === "done") {
    const clean = results.filter((r) => r?.passed).length;
    return (
      <div
        className="rounded-[28px] border-[3px] border-ink bg-white p-7 text-center"
        style={inkShadow}
      >
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          Round complete
        </p>
        <h2 className="mt-2 font-display text-[clamp(28px,5vw,40px)] font-black tracking-tight text-ink">
          {clean} / {round.length} clean
        </h2>
        <ul className="mx-auto mt-5 max-w-[440px] space-y-1.5 text-left">
          {round.map((t, i) => {
            const r = results[i];
            return (
              <li key={t.id} className="font-body text-[14px] text-ink-soft">
                <span className={r?.passed ? "text-success" : "text-flag-red-deep"}>
                  {r?.passed ? "✓" : "✕"}
                </span>{" "}
                <span className="font-bold text-ink">{t.title}</span>
                {r && !r.passed && (
                  <span>
                    {" — "}
                    {!r.constraintMet
                      ? (r.constraintNote ?? "task not completed")
                      : `${r.slips.length} slip${r.slips.length === 1 ? "" : "s"}`}
                  </span>
                )}
                {!r && <span> — skipped</span>}
              </li>
            );
          })}
        </ul>
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

  const solved = verdict !== null;
  // Anchor each slip inside the transcript so the error shows in context;
  // a quote the judge paraphrased falls back to plain text on its card.
  const slipView = verdict
    ? segmentTranscript(
        verdict.transcript,
        verdict.slips.map((s) => s.quote),
        verdict.hitQuotes ?? []
      )
    : null;
  const tint = !solved
    ? "border-ink bg-white"
    : verdict.passed
      ? "border-success bg-success-soft"
      : "border-flag-red bg-flag-red-soft";

  return (
    <div>
      {/* FLOW-001: the Flow page shows its own progress — hide this round's. */}
      {!flow && (
        <div className="mb-3 flex items-center justify-between">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            Task {index + 1} / {round.length}
          </p>
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {results.filter((r) => r?.passed).length} ✓
          </p>
        </div>
      )}

      <div
        className={`rounded-[28px] border-[3px] p-7 transition-colors ${tint}`}
        style={inkShadow}
      >
        <p className="text-center font-body text-[11px] font-black uppercase tracking-[0.24em] text-flag-red">
          {task.title}
        </p>
        <p className="mx-auto mt-3 max-w-[480px] text-center font-body text-[17px] leading-relaxed text-ink">
          {onGloss ? (
            <Glossable text={task.prompt} onGloss={onGloss} onAdd={onAdd} />
          ) : (
            task.prompt
          )}
        </p>

        {!solved ? (
          <div className="mt-7 flex flex-col items-center gap-3">
            {/* The vocab nudge: mounted above the checking/record split so it
                stays put (no refetch) while a submit is in flight or fails.
                A "Try again" clears `verdict`, which remounts this via
                `key={task.id}` and fetches once more per retry — accepted. */}
            {onNudge && (
              <VocabNudgePill key={task.id} fetch={() => onNudge(task.id)} />
            )}
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
                        ? "animate-pulse border-flag-red-deep bg-white text-flag-red"
                        : "border-flag-red-deep bg-flag-red text-white"
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
                      className="btn-3d inline-flex h-[52px] w-[52px] items-center justify-center rounded-[20px] border-[3px] border-ink bg-white font-display text-[18px] font-black text-ink"
                      style={inkShadow}
                    >
                      ✕
                    </button>
                  )}
                </div>
                <p className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                  {recording
                    ? "speak, then tap stop — ✕ discards"
                    : "take a breath, plan your sentences, tap record"}
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
            {failed && (
              <p className="text-center font-body text-[13px] font-semibold text-flag-red-deep">
                {failed}
              </p>
            )}
          </div>
        ) : verdict.gaveUp ? (
          // FLOW-002: a modest feedback state — there was no recording to
          // transcribe and no judge call, so the full transcript/slips UI
          // below (built for a real attempt) would just show empty/blank.
          <div className="mt-6 text-center">
            <p className="font-body text-[14px] font-semibold text-flag-red-deep">
              {verdict.constraintNote}
            </p>
            <div className="mt-6 flex items-center justify-center">
              <button
                type="button"
                onClick={next}
                className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-white px-6 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
                style={inkShadow}
              >
                {flow ? "Next" : index + 1 >= round.length ? "Finish" : "Next"}
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-6">
            {/* The raw transcript IS part of the exercise — what you actually
                said, not what you meant to say. */}
            <div className="rounded-[18px] border-[3px] border-ink bg-white px-4 py-3">
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                What we heard
              </p>
              <p className="mt-1 font-body text-[15px] leading-relaxed text-ink">
                {(slipView?.segments ?? []).map((seg, i) => {
                  if (seg.kind === "plain") return <span key={i}>{seg.text}</span>;
                  if (seg.kind === "hit") {
                    return (
                      <span
                        key={i}
                        className="rounded-[6px] bg-success-soft px-1 py-0.5 box-decoration-clone"
                      >
                        {seg.text}
                        <sup className="ml-1 inline-flex h-[15px] w-[15px] items-center justify-center rounded-full bg-success font-body text-[9px] font-black text-white">
                          ✓
                        </sup>
                      </span>
                    );
                  }
                  return (
                    <span
                      key={i}
                      className="rounded-[6px] bg-flag-red-soft px-1 py-0.5 box-decoration-clone"
                    >
                      {seg.text}
                      <sup className="ml-1 inline-flex h-[15px] w-[15px] items-center justify-center rounded-full bg-flag-red font-body text-[9px] font-black text-white">
                        {(seg.index ?? 0) + 1}
                      </sup>
                    </span>
                  );
                })}
              </p>
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-center gap-2.5">
              <span
                className={`inline-flex items-center gap-1.5 rounded-full border-[2px] px-3 py-1 font-body text-[11px] font-black uppercase tracking-[0.14em] ${
                  verdict.constraintMet
                    ? "border-success bg-success-soft text-success"
                    : "border-flag-red bg-white text-flag-red-deep"
                }`}
              >
                {verdict.constraintMet ? "✓ Task done" : "✕ Task"}
              </span>
              {verdict.hits > 0 && (
                <span className="inline-flex items-center gap-1.5 rounded-full border-[2px] border-ink bg-white px-3 py-1 font-body text-[11px] font-black uppercase tracking-[0.14em] text-ink">
                  structure fired ×{verdict.hits}
                </span>
              )}
            </div>
            {!verdict.constraintMet && verdict.constraintNote && (
              <p className="mt-2 text-center font-body text-[14px] font-semibold text-ink">
                {verdict.constraintNote}
              </p>
            )}

            {verdict.slips.length > 0 && (
              <ul className="mx-auto mt-4 max-w-[480px] space-y-3">
                {verdict.slips.map((s, i) => (
                  <li
                    key={i}
                    className="rounded-[16px] border-[3px] border-ink bg-white px-4 py-3"
                  >
                    <div className="flex items-start gap-2.5">
                      <span className="mt-1 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-flag-red font-body text-[10px] font-black text-white">
                        {i + 1}
                      </span>
                      <div>
                        {/* The corrected sentence, with only the changed words
                            marked: bold + green underline = the fix, a faded
                            ×word = drop this word. */}
                        <p className="font-body text-[15px] leading-relaxed text-ink">
                          {diffWords(s.quote, s.corrected).map((t, k) =>
                            t.kind === "ghost" ? (
                              <span
                                key={k}
                                className="mr-1.5 align-middle font-body text-[11px] font-semibold text-ink-faint"
                              >
                                ×{t.word}
                              </span>
                            ) : (
                              <span
                                key={k}
                                className={
                                  t.kind === "added"
                                    ? "mr-1.5 font-black underline decoration-success decoration-[2.5px] underline-offset-4"
                                    : "mr-1.5"
                                }
                              >
                                {t.word}
                              </span>
                            )
                          )}
                        </p>
                        {/* No transcript anchor for this slip (judge
                            paraphrased) — show their words plainly instead. */}
                        {!(slipView?.matched[i] ?? false) && (
                          <p className="mt-1 font-body text-[12px] text-ink-muted">
                            you said: “{s.quote}”
                          </p>
                        )}
                        <p className="mt-1.5 font-body text-[12px] font-semibold text-ink-soft">
                          <span className="mr-1.5 font-body text-[10px] font-black uppercase tracking-[0.18em] text-flag-red-deep">
                            why
                          </span>
                          {s.note}
                        </p>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {/* IDIOM-002 P1: on-demand phrasing advice on the whole take —
                separate from the slips above, which grade grammar. */}
            {verdict.transcript.trim() !== "" && (
              <GermanWay
                text={verdict.transcript}
                context={task.prompt}
                onGloss={onGloss}
                onAdd={onAdd}
                sessionId={sessionId}
              />
            )}

            <div className="mt-6 flex items-center justify-center gap-4">
              <button
                type="button"
                onClick={startRecording}
                className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red"
              >
                ↻ Try again
              </button>
              <button
                type="button"
                onClick={next}
                className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-white px-6 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
                style={inkShadow}
              >
                {flow ? "Next" : index + 1 >= round.length ? "Finish" : "Next"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
