"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { Scenario, StructureResult } from "./api";
import { WordRejectedError } from "../satzschmiede/api";

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

const SATZ_AMPEL_DOT: Record<"green" | "yellow" | "red", string> = {
  green: "bg-success",
  // No flag-yellow design token exists yet — plain Tailwind amber stands in.
  yellow: "bg-amber-500",
  red: "bg-flag-red",
};

// Shown once, before the first question — same one-time-rules convention as
// VerbindungenTrainer / BauteilTrainer. The wording carries the whole point
// of the exercise: this is a structure lens, not a grammar check.
const RULES: { title: string; body: string }[] = [
  {
    title: "One question, one shot",
    body: "Someone in the scene asks you something. Answer out loud — one recording, then it's submitted.",
  },
  {
    title: "Structure, not grammar",
    body: "We're not checking your cases or endings here. We're checking whether your answer holds together.",
  },
  {
    title: "Answer first",
    body: "Open with the point — the anchor. Save the reasons and the details for after it.",
  },
  {
    title: "One idea per sentence",
    body: "A sentence stacking three thoughts together reads as a run-on, even if every word is correct. Cut it into two.",
  },
  {
    title: "Close clean",
    body: "Land the last sentence instead of trailing off into another clause.",
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
}) {
  const [phase, setPhase] = useState<Phase>(initialPhase);
  const [recording, setRecording] = useState(false);
  const [checking, setChecking] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [verdict, setVerdict] = useState<StructureResult | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

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
      if (err instanceof WordRejectedError) {
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
          onClick={() => {
            onStart();
            setPhase("scene");
          }}
          className="btn-3d mt-7 inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-white"
          style={redShadow}
        >
          Start
        </button>
      </div>
    );
  }

  if (phase === "result" && verdict) {
    return (
      <div>
        <div className="mb-3 flex items-center justify-between">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {scenario.persona.name} asked
          </p>
        </div>

        <div
          className="rounded-[28px] border-[3px] border-ink bg-white p-7"
          style={inkShadow}
        >
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
                verdict.anchor.present
                  ? "border-success bg-success-soft text-success"
                  : "border-flag-red bg-white text-flag-red-deep"
              }`}
            >
              {verdict.anchor.present ? "✓ Anchor" : "✕ Anchor"}
            </span>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border-[2px] px-3 py-1 font-body text-[11px] font-black uppercase tracking-[0.14em] ${
                verdict.close.clean
                  ? "border-success bg-success-soft text-success"
                  : "border-flag-red bg-white text-flag-red-deep"
              }`}
            >
              {verdict.close.clean ? "✓ Clean close" : "✕ Close"}
            </span>
          </div>
          {verdict.anchor.note && (
            <p className="mt-2 text-center font-body text-[13px] font-semibold text-ink-soft">
              {verdict.anchor.note}
            </p>
          )}
          {verdict.close.note && (
            <p className="mt-1 text-center font-body text-[13px] font-semibold text-ink-soft">
              {verdict.close.note}
            </p>
          )}

          {/* Satz-Ampel — one row per sentence, colored by how clean an idea
              it carries; a non-null `cut` names where an overloaded sentence
              should split. */}
          {verdict.sentences.length > 0 && (
            <div className="mt-6">
              <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                Satz-Ampel
              </p>
              <ul className="mt-2 space-y-2.5">
                {verdict.sentences.map((s, i) => (
                  <li
                    key={i}
                    className="rounded-[16px] border-[3px] border-ink bg-white px-4 py-3"
                  >
                    <div className="flex items-start gap-2.5">
                      <span
                        aria-hidden
                        className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${SATZ_AMPEL_DOT[s.color]}`}
                      />
                      <p className="font-body text-[15px] leading-relaxed text-ink">
                        {s.text}
                      </p>
                    </div>
                    {s.cut && (
                      <p className="mt-1.5 pl-5 font-body text-[12px] font-semibold text-ink-muted">
                        split here → {s.cut}
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
          <div className="mt-6 rounded-[18px] border-[3px] border-ink bg-white px-4 py-4">
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
                      className="rounded-full border-[2px] border-ink bg-white px-4 py-1.5 font-body text-[13px] font-black tracking-wide text-ink"
                    >
                      {w}
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>

          <p className="mt-6 text-center font-body text-[16px] font-bold leading-relaxed text-ink">
            {verdict.takeaway}
          </p>

          <div className="mt-7 flex items-center justify-center gap-5">
            <button
              type="button"
              onClick={onNewQuestion}
              className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-white"
              style={redShadow}
            >
              New question
            </button>
            <Link
              href="/practice"
              className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
            >
              ← Back to menu
            </Link>
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
        className="rounded-[28px] border-[3px] border-ink bg-white p-7"
        style={inkShadow}
      >
        <p className="mx-auto max-w-[480px] text-center font-body text-[14px] italic leading-relaxed text-ink-muted">
          {scenario.kontext}
        </p>
        <p className="mx-auto mt-4 max-w-[480px] text-center font-body text-[20px] font-bold leading-relaxed text-ink">
          {scenario.question}
        </p>

        <div className="mt-7 flex flex-col items-center gap-3">
          {checking ? (
            <p className="font-body text-[14px] font-semibold text-ink-muted">
              Checking…
            </p>
          ) : (
            <>
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
              <p className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                {recording
                  ? "speak, then tap stop"
                  : "answer first, then explain — tap record"}
              </p>
            </>
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
