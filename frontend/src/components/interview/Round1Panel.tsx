"use client";

import { useState } from "react";
import { useRecorder } from "../shared/recorder";
import type { ComprehensionResult } from "./api";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// Round 1 ("listen & retell") — ported from interview_local/web/round1.js.
// The learner hears the chunk WITHOUT ever seeing the transcript (the
// parent keeps <Transcript> out of the tree entirely while this panel is
// mounted) and retells in German what was said; this panel records that
// retell, uploads it, and renders the comprehension judge's verdict.
//
// After every judged attempt (pass or fail) a result block shows: the
// learner's own transcript ("What we heard"), then the judge's feedback.
// There is no auto-advance — on a pass, or once the second failed attempt
// is exhausted, an explicit Continue button appears; a non-exhausted fail
// leaves only Record available for the retry. A technical failure (the
// judge call itself erroring) never counts as an attempt and never
// disturbs whatever Continue affordance a real verdict already earned.
//
// Remounted by the parent (key={chunk.id}) on every chunk switch — that's
// this component's entire "reset" story; useRecorder's own unmount cleanup
// already discards an in-flight recording without firing onStop.
const MAX_ATTEMPTS = 2;

type Outcome = "pass" | "fail" | "judge_error" | null;

export default function Round1Panel({
  chunkId,
  listenUnlocked,
  onBeforeRecord,
  onSubmit,
  onDone,
}: {
  chunkId: string;
  // player.js's listen-lock: the record button starts locked and unlocks
  // once the learner has listened to >=75% of the chunk audio (or it
  // ends) — see InterviewPlayer's accumulator. Owned by the parent since
  // it tracks the shared <audio> element this panel never touches directly.
  listenUnlocked: boolean;
  // Never record over the interviewer's own voice — pauses the shared
  // <audio> element before the mic opens.
  onBeforeRecord: () => void;
  onSubmit: (chunkId: string, audio: Blob) => Promise<ComprehensionResult>;
  // Fires exactly once, when Continue is clicked.
  onDone: (passed: boolean) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [softError, setSoftError] = useState<string | null>(null); // no_speech
  const [failed, setFailed] = useState<string | null>(null); // network/HTTP failure

  const [attemptCount, setAttemptCount] = useState(0);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [transcript, setTranscript] = useState("");
  const [gotRight, setGotRight] = useState("");
  const [nudge, setNudge] = useState("");
  // null = no Continue affordance yet; set once a real verdict earns one.
  const [pendingPassed, setPendingPassed] = useState<boolean | null>(null);
  const [settled, setSettled] = useState(false);
  const passLocked = pendingPassed === true;

  async function handleStop(blob: Blob) {
    setSubmitting(true);
    setSoftError(null);
    setFailed(null);
    try {
      const res = await onSubmit(chunkId, blob);
      if (res.kind === "no_speech") {
        setSoftError("We couldn't hear anything — try again.");
        return;
      }
      if (res.kind === "judge_error") {
        setTranscript(res.transcript);
        setOutcome("judge_error");
        return; // never counted, never touches pendingPassed
      }
      setTranscript(res.transcript);
      const nextCount = attemptCount + 1;
      setAttemptCount(nextCount);
      setGotRight(res.comprehension.gotRight);
      if (res.comprehension.passed) {
        setOutcome("pass");
        setNudge("");
        setPendingPassed(true);
      } else {
        setOutcome("fail");
        setNudge(res.comprehension.nudge);
        if (nextCount >= MAX_ATTEMPTS) setPendingPassed(false);
      }
    } catch (err) {
      setFailed(
        err instanceof Error && err.message && !err.message.includes("failed (")
          ? err.message
          : "Couldn't check that — try again in a moment."
      );
    } finally {
      setSubmitting(false);
    }
  }

  const recorder = useRecorder(handleStop);

  function startRecording() {
    setSoftError(null);
    setFailed(null);
    onBeforeRecord();
    void recorder.start();
  }

  const recordDisabled =
    submitting || passLocked || settled || (!listenUnlocked && !recorder.recording);

  return (
    <div
      className="rounded-[28px] border-[3px] border-ink bg-white p-7"
      style={inkShadow}
    >
      <p className="text-center font-body text-[14px] leading-relaxed text-ink-soft">
        Listen to the audio, then explain out loud — in German — what you
        understood.
      </p>

      <div className="mt-6 flex flex-col items-center gap-3">
        {submitting ? (
          <p className="font-body text-[14px] font-semibold text-ink-muted">
            Checking…
          </p>
        ) : (
          <>
            <div className="flex items-center gap-3">
              <button
                type="button"
                disabled={recordDisabled}
                title={
                  !listenUnlocked && !recorder.recording ? "Listen first" : undefined
                }
                onClick={recorder.recording ? recorder.stop : startRecording}
                className={`btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] px-7 py-3.5 font-display text-[14px] font-black uppercase tracking-[0.16em] disabled:cursor-not-allowed disabled:opacity-40 ${
                  recorder.recording
                    ? "animate-pulse border-flag-red-deep bg-white text-flag-red"
                    : "border-flag-red-deep bg-flag-red text-white"
                }`}
                style={redShadow}
              >
                {recorder.recording ? `Stop · ${recorder.elapsed}s` : "Record"}
              </button>
              {recorder.recording && (
                <button
                  type="button"
                  onClick={recorder.cancel}
                  aria-label="Discard recording"
                  title="Discard recording"
                  className="btn-3d inline-flex h-[52px] w-[52px] items-center justify-center rounded-[20px] border-[3px] border-ink bg-white font-display text-[18px] font-black text-ink"
                  style={inkShadow}
                >
                  ✕
                </button>
              )}
            </div>
            {!listenUnlocked && !recorder.recording && (
              <p className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                listen to most of the clip to unlock recording
              </p>
            )}
          </>
        )}
        {(recorder.error || failed || softError) && (
          <p className="text-center font-body text-[13px] font-semibold text-flag-red-deep">
            {recorder.error || failed || softError}
          </p>
        )}
      </div>

      {outcome && (
        <div className="mt-6">
          {transcript && (
            <div className="rounded-[18px] border-[3px] border-ink bg-white px-4 py-3">
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                What we heard
              </p>
              <p className="mt-1 font-body text-[15px] leading-relaxed text-ink">
                {transcript}
              </p>
            </div>
          )}

          {outcome === "pass" && (
            <div className="mt-4 rounded-[18px] border-[3px] border-success bg-success-soft px-4 py-3 text-center">
              <p className="font-body text-[14px] font-semibold text-ink">
                <span className="mr-1.5 text-success">✓</span>
                {gotRight || "Got it."}
              </p>
            </div>
          )}

          {outcome === "fail" && (
            <div className="mt-4 rounded-[18px] border-[3px] border-flag-red bg-flag-red-soft px-4 py-3 text-center">
              {gotRight && (
                <p className="font-body text-[14px] font-semibold text-ink">
                  {gotRight}
                </p>
              )}
              <p className="mt-1 font-body text-[14px] text-ink-soft">{nudge}</p>
            </div>
          )}

          {outcome === "judge_error" && (
            <div className="mt-4 rounded-[18px] border-[3px] border-ink-faint bg-paper-warm px-4 py-3 text-center">
              <p className="font-body text-[13px] leading-snug text-ink-soft">
                Couldn&apos;t grade this attempt — it doesn&apos;t count.
              </p>
            </div>
          )}
        </div>
      )}

      {pendingPassed !== null && (
        <div className="mt-6 flex items-center justify-center">
          <button
            type="button"
            disabled={settled}
            onClick={() => {
              if (settled) return;
              setSettled(true);
              onDone(pendingPassed);
            }}
            className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-white disabled:opacity-60"
            style={redShadow}
          >
            Continue →
          </button>
        </div>
      )}
    </div>
  );
}
