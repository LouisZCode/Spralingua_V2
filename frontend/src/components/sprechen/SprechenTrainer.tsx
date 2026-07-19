"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { SpokenTask, SprechenVerdict } from "./api";

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
}: {
  round: SpokenTask[];
  // Judge one recorded clip (POST /sprechen/attempts via the parent, which
  // owns the token and the OBS-007 practice-session id).
  onAttempt: (taskId: string, audio: Blob) => Promise<SprechenVerdict>;
  // Fetch a fresh round; the parent remounts this component with it.
  onNewRound: () => void;
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
  const tint = !solved
    ? "border-ink bg-white"
    : verdict.passed
      ? "border-success bg-success-soft"
      : "border-flag-red bg-flag-red-soft";

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          Task {index + 1} / {round.length}
        </p>
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          {results.filter((r) => r?.passed).length} ✓
        </p>
      </div>

      <div
        className={`rounded-[28px] border-[3px] p-7 transition-colors ${tint}`}
        style={inkShadow}
      >
        <p className="text-center font-body text-[11px] font-black uppercase tracking-[0.24em] text-flag-red">
          {task.title}
        </p>
        <p className="mx-auto mt-3 max-w-[480px] text-center font-body text-[17px] leading-relaxed text-ink">
          {task.prompt}
        </p>

        {!solved ? (
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
                </p>
              </>
            )}
            {failed && (
              <p className="text-center font-body text-[13px] font-semibold text-flag-red-deep">
                {failed}
              </p>
            )}
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
                {verdict.transcript}
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
                    <p className="font-body text-[14px] text-ink-soft">
                      <span className="line-through decoration-flag-red decoration-2">
                        {s.quote}
                      </span>
                    </p>
                    <p className="mt-1 font-body text-[15px] font-bold text-ink">
                      {s.corrected}
                    </p>
                    <p className="mt-1 font-body text-[12px] font-semibold text-ink-muted">
                      {s.note}
                    </p>
                  </li>
                ))}
              </ul>
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
                {index + 1 >= round.length ? "Finish" : "Next"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
