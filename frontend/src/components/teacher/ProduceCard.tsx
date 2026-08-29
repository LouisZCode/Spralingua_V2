"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// CLARA-16: the live forge's new shape — a production task ("Make a wish
// about your weekend using 'hätte'"), answered with ONE complete German
// sentence, typed OR spoken. Replaces CLARA-15's forge card (which mounted
// VerbindungenTrainer on a native single-gap item); this is a bespoke
// single-item card, not a round-of-one mount of an existing trainer, so it
// owns its own layout/verdict copy rather than reusing a `round`/`flow`/
// `hideContinue` trainer contract. TeacherChat.tsx owns the network calls
// and the ⟦ÜBUNGSERGEBNIS⟧ report (same split as every other exercise mount:
// "closures own the network + report side; the card owns UI state") — this
// file only renders `item`/`topic` and calls back into the three closures
// it's handed.

export type ProduceItem = {
  id: string;
  task: string; // the English instruction
  target: string; // the German word(s)/structure the sentence must contain
  hint: string; // a one-line English rule note, reveal-on-tap
};

export type ProduceVerdict = {
  correct: boolean;
  note: string | null;
  corrected: string | null; // the learner's own sentence minimally fixed — only when wrong
  example: string; // a model answer — always present, the learning payoff
  gaveUp?: true;
};

// Local shape for whatever's currently graded — a spoken attempt's verdict
// additionally carries the transcript STT heard; a typed or give-up verdict
// doesn't, so `answer` (the textarea's own last-submitted value) is the
// fallback for "what did they say" in that case. See `attemptText` below.
type Verdict = ProduceVerdict & { transcript?: string };

// Attempts here fail closed on two different things: a normal network/judge
// hiccup (transient — inputs re-enable, same "try again" every other card
// shows) and a 404 (the item expired server-side — nothing left to retry
// against, so the card disables itself with the server's own message). The
// two closures below (postAttempt/postAttemptAudio in TeacherChat.tsx) stamp
// the HTTP status onto the thrown Error so this file can tell them apart
// without TeacherChat needing to know anything about how this card renders
// that distinction.
type HttpError = Error & { status?: number };

const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// Mirrors sprechen/SprechenTrainer.tsx's MediaRecorder mechanics exactly
// (mime selection, stream cleanup, permission handling) — that file is
// read-only for this round, so the working bits are duplicated here rather
// than imported (every trainer in this app declares its own copy of these
// same two consts rather than sharing a module, so this follows the
// existing convention too).
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
// One sentence, not sprechen's multi-sentence answer — a much lower ceiling
// than sprechen's 90s still generously covers a forgotten stop tap.
const MAX_RECORD_SECONDS = 30;

// Splits `task` on every literal (case-insensitive) occurrence of `target`,
// bolding the matches in place. `found` is false when the target string
// never appears verbatim in the instruction (e.g. the task paraphrases it),
// in which case the caller renders a separate "Use: …" chip instead.
function renderTaskWithTarget(
  task: string,
  target: string
): { nodes: React.ReactNode; found: boolean } {
  const trimmed = target.trim();
  if (!trimmed) return { nodes: task, found: true };
  const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = task.split(new RegExp(`(${escaped})`, "gi"));
  if (parts.length === 1) return { nodes: task, found: false };
  const targetLower = trimmed.toLowerCase();
  return {
    nodes: parts.map((part, i) =>
      part.toLowerCase() === targetLower ? (
        <strong key={i} className="font-black text-flag-gold-deep">
          {part}
        </strong>
      ) : (
        <span key={i}>{part}</span>
      )
    ),
    found: true,
  };
}

export default function ProduceCard({
  item,
  topic,
  onAttempt,
  onAttemptAudio,
  onGiveUp,
}: {
  item: ProduceItem;
  topic: string;
  // Typed attempt — POST .../attempts with {drill:"produce", itemId, answer}
  // (the parent owns itemId; this file never sees it).
  onAttempt: (answer: string) => Promise<ProduceVerdict>;
  // Spoken attempt — POST .../attempts-audio, multipart, drill:"produce".
  // Same verdict shape plus the transcript STT heard.
  onAttemptAudio: (audio: Blob) => Promise<ProduceVerdict & { transcript: string }>;
  // Give-up — POST .../attempts with give_up:true, no text of the learner's
  // own to show; the verdict's `example` is the payoff.
  onGiveUp: () => Promise<ProduceVerdict>;
}) {
  const [answer, setAnswer] = useState("");
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [checking, setChecking] = useState(false);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [hintShown, setHintShown] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  // Set only on a 404 (item expired) — distinct from `failed`: this DISABLES
  // the card instead of leaving the inputs live for a retry, per CLARA-16's
  // spec ("a 404 expired item instead disables the card with its message").
  const [expired, setExpired] = useState<string | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const discardRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (verdict === null && !expired) textareaRef.current?.focus();
  }, [verdict, expired]);

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

  // Hard cap — a forgotten open mic auto-submits at the limit.
  useEffect(() => {
    if (recording && elapsed >= MAX_RECORD_SECONDS) stopRecording();
  }, [recording, elapsed, stopRecording]);

  // Unmount mid-recording (a fresh deal remounts this via `key`, or the
  // session ends): kill the mic, never submit the orphaned clip.
  useEffect(() => {
    return () => {
      discardRef.current = true;
      if (timerRef.current) clearInterval(timerRef.current);
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
    };
  }, []);

  function handleError(err: unknown) {
    console.warn("Produce attempt failed:", err);
    const status = err instanceof Error ? (err as HttpError).status : undefined;
    if (status === 404) {
      setExpired(
        err instanceof Error && err.message
          ? err.message
          : "This exercise expired."
      );
      return;
    }
    setFailed("Couldn't check that one — try again");
  }

  async function submitTyped() {
    const trimmed = answer.trim();
    if (!trimmed || checking || recording) return;
    setChecking(true);
    setFailed(null);
    try {
      const res = await onAttempt(trimmed);
      setVerdict(res);
    } catch (err) {
      handleError(err);
    } finally {
      setChecking(false);
    }
  }

  async function submitAudio(audio: Blob) {
    setChecking(true);
    setFailed(null);
    try {
      const res = await onAttemptAudio(audio);
      setVerdict(res);
    } catch (err) {
      handleError(err);
    } finally {
      setChecking(false);
    }
  }

  async function giveUp() {
    if (checking || recording) return;
    setChecking(true);
    setFailed(null);
    try {
      const res = await onGiveUp();
      setVerdict(res);
    } catch (err) {
      handleError(err);
    } finally {
      setChecking(false);
    }
  }

  async function startRecording() {
    if (checking || verdict) return;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      // Mic denied/unavailable — no error UI, the typed path still fully
      // works, per CLARA-16's spec.
      console.warn("Produce mic permission denied:", e);
      return;
    }
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
        void submitAudio(blob);
      };
      recorderRef.current = rec;
      rec.start();
      setRecording(true);
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    } catch (e) {
      stream.getTracks().forEach((t) => t.stop());
      console.warn("Produce mic recorder init failed:", e);
    }
  }

  if (expired) {
    return (
      <div
        className="rounded-[28px] border-[3px] border-line bg-card p-7 text-center"
        style={inkShadow}
      >
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          Expired
        </p>
        <p className="mt-3 font-body text-[14px] font-semibold text-ink-soft">
          {expired}
        </p>
      </div>
    );
  }

  const solved = verdict !== null;
  const tint = !solved
    ? "border-line bg-card"
    : verdict.correct
      ? "border-success bg-success-soft"
      : "border-flag-red bg-flag-red-soft";
  const { nodes: taskNodes, found: targetFound } = renderTaskWithTarget(
    item.task,
    item.target
  );
  // A spoken attempt carries its own transcript; a typed (or give-up)
  // verdict has none, so the textarea's last-submitted value stands in.
  const attemptText = verdict?.transcript ?? answer;

  return (
    <div
      className={`rounded-[28px] border-[3px] p-7 transition-colors ${tint}`}
      style={inkShadow}
    >
      <p className="text-center font-body text-[11px] font-black uppercase tracking-[0.24em] text-flag-red">
        {topic}
      </p>
      <p className="mx-auto mt-3 max-w-[480px] text-center font-body text-[17px] leading-relaxed text-ink">
        {taskNodes}
      </p>
      {!targetFound && item.target.trim() && (
        <p className="mt-3 text-center">
          <span className="inline-flex items-center gap-1.5 rounded-full border-[2px] border-line bg-card px-3 py-1 font-body text-[11px] font-black uppercase tracking-[0.14em] text-flag-gold-deep">
            Use: {item.target}
          </span>
        </p>
      )}

      {solved || hintShown ? (
        <p className="mt-2 text-center font-body text-[13px] italic text-ink-muted">
          {item.hint}
        </p>
      ) : (
        <p className="mt-2 text-center">
          <button
            type="button"
            onClick={() => setHintShown(true)}
            className="font-body text-[11px] font-bold uppercase tracking-[0.2em] text-ink-faint underline-offset-2 hover:text-ink-muted hover:underline"
          >
            Hint
          </button>
        </p>
      )}

      {!solved ? (
        <div className="mt-6 flex flex-col items-center gap-3">
          <textarea
            ref={textareaRef}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submitTyped();
              }
            }}
            placeholder="Type your German sentence…"
            lang="de"
            autoCapitalize="off"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            rows={2}
            maxLength={240}
            disabled={checking || recording}
            className="w-full min-w-0 resize-none rounded-[18px] border-[3px] border-line bg-card px-4 py-3 font-body text-[16px] text-ink outline-none placeholder:text-ink-faint focus:border-flag-red disabled:opacity-60"
          />
          <div className="flex flex-wrap items-center justify-center gap-3">
            <button
              type="button"
              onClick={() => void submitTyped()}
              disabled={checking || recording || !answer.trim()}
              className="btn-3d inline-flex items-center justify-center rounded-[18px] border-[3px] border-red-line bg-flag-red-fill px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-on-fill disabled:pointer-events-none disabled:opacity-40"
              style={redShadow}
            >
              {checking ? "Checking…" : "Check"}
            </button>
            <button
              type="button"
              onClick={recording ? stopRecording : () => void startRecording()}
              disabled={checking && !recording}
              aria-pressed={recording}
              className={`btn-3d inline-flex items-center gap-2 rounded-[18px] border-[3px] px-5 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] disabled:pointer-events-none disabled:opacity-40 ${
                recording
                  ? "animate-pulse border-red-line bg-card text-flag-red"
                  : "border-line bg-card text-ink"
              }`}
              style={recording ? redShadow : inkShadow}
            >
              {recording ? `Stop · ${elapsed}s` : "Record"}
            </button>
          </div>
          {recording && (
            <p className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
              speak your sentence, then tap stop
            </p>
          )}
          {/* Quiet styling — never a rival to Check/Record, mirrors every
              other card's give-up affordance. */}
          <button
            type="button"
            onClick={() => void giveUp()}
            disabled={checking || recording}
            className="font-body text-[11px] text-ink-muted underline-offset-2 hover:underline disabled:pointer-events-none disabled:opacity-40"
          >
            Show me an example
          </button>
          {failed && (
            <p className="text-center font-body text-[13px] font-semibold text-flag-red-deep">
              {failed}
            </p>
          )}
        </div>
      ) : (
        <div className="mt-6">
          {!verdict.gaveUp && (
            <p className="mx-auto max-w-[440px] text-center font-body text-[13px] text-ink-soft">
              {verdict.transcript ? "You said: " : "You wrote: "}
              {/* Never struck through / crossed out — the wrong sentence is
                  shown plainly, color-neutral, exactly as they gave it. */}
              <span className="font-semibold text-ink">{attemptText}</span>
            </p>
          )}

          {verdict.gaveUp ? (
            <>
              <p className="mx-auto mt-1 max-w-[440px] text-center font-body text-[14px] leading-relaxed text-ink">
                <span className="mr-1.5 font-body text-[11px] font-black uppercase tracking-[0.12em] text-flag-gold-deep">
                  A good answer
                </span>
                {verdict.example}
              </p>
              {verdict.note && (
                <p className="mx-auto mt-3 max-w-[440px] text-center font-body text-[13px] leading-relaxed text-ink-soft">
                  {verdict.note}
                </p>
              )}
            </>
          ) : verdict.correct ? (
            <>
              {verdict.note && (
                <p className="mx-auto mt-3 max-w-[440px] text-center font-body text-[13px] leading-relaxed text-ink-soft">
                  {verdict.note}
                </p>
              )}
              <p className="mx-auto mt-3 max-w-[440px] text-center font-body text-[14px] leading-relaxed text-ink-muted">
                <span className="mr-1.5 font-body text-[11px] font-black uppercase tracking-[0.12em] text-flag-gold-deep">
                  Another good one
                </span>
                {verdict.example}
              </p>
            </>
          ) : (
            <>
              {verdict.note && (
                <p className="mx-auto mt-3 max-w-[440px] text-center font-body text-[13px] leading-relaxed text-ink-soft">
                  {verdict.note}
                </p>
              )}
              {verdict.corrected && (
                <p className="mx-auto mt-3 max-w-[440px] text-center font-body text-[15px] font-bold leading-snug text-ink">
                  <span className="mr-1.5 text-flag-red-deep">→</span>
                  <span className="mr-1.5 font-body text-[11px] font-black uppercase tracking-[0.12em] text-success">
                    Better
                  </span>
                  {verdict.corrected}
                </p>
              )}
              <p className="mx-auto mt-3 max-w-[440px] text-center font-body text-[14px] leading-relaxed text-ink-muted">
                <span className="mr-1.5 font-body text-[11px] font-black uppercase tracking-[0.12em] text-flag-gold-deep">
                  Another good one
                </span>
                {verdict.example}
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
