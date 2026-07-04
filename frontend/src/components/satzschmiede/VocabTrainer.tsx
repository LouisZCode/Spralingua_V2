"use client";

import { useEffect, useRef, useState } from "react";
import { WordRejectedError, type AttemptResult } from "./api";
import type { Card, CardType } from "./deck";

// Visual identity per word type: badge label + colour. One card shell serves
// every type — the face shows only the badge + the word to produce. Meaning and
// grammar stay hidden until the card flips (recall the gender/usage, don't read
// it off the card).
const TYPE_STYLE: Record<CardType, { label: string; chip: string }> = {
  noun: { label: "Noun", chip: "bg-flag-red text-white" },
  verb: { label: "Verb", chip: "bg-flag-gold text-ink" },
  phrase: { label: "Phrase", chip: "bg-ink text-white" },
};

type Verdict = "correct" | "close" | "revealed";

// The answer side tints the whole card by outcome, so the result reads at a
// glance without scrolling.
const VERDICT_STYLE: Record<
  Verdict,
  { label: string; faceBox: string; tone: string }
> = {
  correct: {
    label: "Correct",
    faceBox: "border-success bg-success-soft",
    tone: "text-success",
  },
  close: {
    label: "Close — not quite",
    faceBox: "border-flag-red bg-flag-red-soft",
    tone: "text-flag-red-deep",
  },
  revealed: {
    label: "Revealed — counts as a miss",
    faceBox: "border-flag-gold bg-flag-gold-soft",
    tone: "text-flag-gold-deep",
  },
};

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// One sentence is all we judge — auto-stop keeps a forgotten open mic from
// uploading minutes of audio (the backend caps bytes for the same reason).
const MAX_RECORD_SECONDS = 20;

// Chrome/Firefox record opus-in-webm, Safari aac-in-mp4 — Deepgram takes both
// as-is, so we just pick the first container the browser supports.
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

export default function VocabTrainer({
  deck,
  onRemove,
  onAttempt,
}: {
  deck: Card[];
  // Drop the current card from the pool; the parent refetches the deck and
  // this prop shrinks — the clamped index below then lands on the next card.
  onRemove: (cardId: string) => Promise<void>;
  // Judge one recorded sentence (POST /satz/attempts via the parent, which
  // owns the token). Resolves with the examiner's verdict.
  onAttempt: (cardId: string, audio: Blob) => Promise<AttemptResult>;
}) {
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false); // used the hint → a miss
  const [flipped, setFlipped] = useState(false); // showing the answer side
  const [removing, setRemoving] = useState(false);

  const [recording, setRecording] = useState(false);
  const [checking, setChecking] = useState(false); // clip uploaded, verdict pending
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<AttemptResult | null>(null);
  const [attemptError, setAttemptError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  // Set when the clip must NOT be submitted (unmount mid-recording).
  const discardRef = useRef(false);

  const total = deck.length;
  // The deck can shrink underneath us (a removal refetch) while index still
  // points past the end — clamp instead of crashing on deck[index].
  const safeIndex = Math.min(index, total - 1);
  const card = deck[safeIndex];
  const accent = TYPE_STYLE[card.type];

  // Card navigation stays locked while a recording or check is in flight, so
  // the verdict that comes back always belongs to the card on screen.
  const busy = recording || checking;

  const verdict: Verdict = revealed ? "revealed" : (result?.verdict ?? "close");
  const v = VERDICT_STYLE[verdict];

  // Nouns hide the article, reflexive verbs hide `sich` — the same move: a
  // grammatical lead stripped from the clue and restored here on the answer so
  // the learner sees what they were meant to recall.
  const lead = card.article ?? (card.reflexive ? "sich" : undefined);
  const fullForm = lead ? `${lead} ${card.target}` : card.target;

  // Recording timer + auto-stop.
  useEffect(() => {
    if (!recording) return;
    setElapsed(0);
    const started = Date.now();
    const tick = setInterval(() => {
      const s = Math.floor((Date.now() - started) / 1000);
      setElapsed(s);
      if (s >= MAX_RECORD_SECONDS) stopRecording();
    }, 250);
    return () => clearInterval(tick);
  }, [recording]);

  // Unmount mid-recording: kill the mic without submitting the partial clip.
  useEffect(
    () => () => {
      discardRef.current = true;
      if (recorderRef.current?.state === "recording") {
        recorderRef.current.stop();
      }
    },
    []
  );

  async function startRecording() {
    if (busy || flipped) return;
    setAttemptError(null);
    setResult(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setAttemptError(
        "Microphone blocked — allow mic access in your browser and try again."
      );
      return;
    }
    const mime = MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m));
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      setRecording(false);
      if (discardRef.current) return;
      void check(new Blob(chunks, { type: rec.mimeType || "audio/webm" }));
    };
    discardRef.current = false;
    recorderRef.current = rec;
    rec.start();
    setRecording(true);
  }

  function stopRecording() {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
    }
  }

  async function check(audio: Blob) {
    setChecking(true);
    try {
      const res = await onAttempt(card.id, audio);
      setResult(res);
      setFlipped(true);
    } catch (err) {
      if (err instanceof WordRejectedError) {
        // Learner-facing sentence from the backend ("We couldn't hear
        // anything…") — show it verbatim.
        setAttemptError(err.message);
      } else {
        setAttemptError("The check hiccuped — try again in a moment.");
      }
    } finally {
      setChecking(false);
    }
  }

  // Reset the per-card scratch state on every move. Wrap around so browsing
  // the deck never dead-ends.
  function goTo(next: number) {
    if (busy) return;
    setIndex((next + total) % total);
    setRevealed(false);
    setFlipped(false);
    setResult(null);
    setAttemptError(null);
  }

  async function handleRemove() {
    if (removing || busy) return;
    setRemoving(true);
    try {
      await onRemove(card.id);
      // The shrunken deck arrives via props; reset the scratch state so the
      // card that slides into this slot starts fresh.
      setRevealed(false);
      setFlipped(false);
      setResult(null);
      setAttemptError(null);
    } finally {
      setRemoving(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-xl">
      {/* Progress + free browse */}
      <div className="mb-4 flex items-center justify-between">
        <button
          type="button"
          onClick={() => goTo(safeIndex - 1)}
          disabled={busy}
          className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red disabled:opacity-40"
        >
          ← Prev
        </button>
        <span className="font-body text-[11px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
          Word {safeIndex + 1} of {total}
        </span>
        <button
          type="button"
          onClick={() => goTo(safeIndex + 1)}
          disabled={busy}
          className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red disabled:opacity-40"
        >
          Next →
        </button>
      </div>

      {/* The card — word only. Flips in place; the mic below never moves. */}
      <div className="flip-scene h-[360px]">
        <div className={`flip-card ${flipped ? "is-flipped" : ""}`}>
          {/* ── Front: the word to produce ──────────────────────────── */}
          <div className="flip-face items-center justify-center rounded-[28px] border-[3px] border-ink bg-white p-7 text-center shadow-[0_6px_0_var(--color-ink)]">
            <span
              className={`inline-flex items-center rounded-full border-[2px] border-ink px-3 py-1 font-body text-[11px] font-black uppercase tracking-[0.18em] ${accent.chip}`}
            >
              {accent.label}
            </span>
            <h2 className="mt-5 font-display text-[clamp(30px,6vw,44px)] font-black leading-[1.05] tracking-tight text-ink">
              {card.target}
            </h2>
          </div>

          {/* ── Back: verdict + the info that was hidden ────────────── */}
          <div
            className={`flip-face flip-back rounded-[28px] border-[3px] p-7 shadow-[0_6px_0_var(--color-ink)] ${v.faceBox}`}
          >
            <div className="flex min-h-0 flex-1 flex-col">
              <p
                className={`font-display text-[16px] font-black uppercase tracking-[0.14em] ${v.tone}`}
              >
                {v.label}
              </p>
              <p className="mt-2 font-display text-[22px] font-black leading-tight text-ink">
                {fullForm}
              </p>
              <dl className="mt-3 min-h-0 flex-1 space-y-2.5 overflow-y-auto">
                {result && (
                  <Row label="We heard" value={`„${result.transcript}“`} />
                )}
                {result && <Row label="Feedback" value={result.feedback} />}
                {result?.corrected && (
                  <Row label="Try it like this" value={result.corrected} />
                )}
                <Row label="Meaning" value={card.gloss} />
                {card.note && <Row label="Gender / form" value={card.note} />}
                {card.example && (
                  <Row label="Natural example" value={card.example} />
                )}
              </dl>
            </div>
            <button
              type="button"
              onClick={() => goTo(safeIndex + 1)}
              className="btn-3d mt-3 inline-flex w-full items-center justify-center gap-2 rounded-[20px] border-[3px] border-ink bg-white px-5 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
              style={inkShadow}
            >
              Next word →
            </button>
          </div>
        </div>
      </div>

      {/* ── Separate block, outside the card: the mic. Speak one sentence,
          release, verdict flips the card. Mic-only by design — no typing. ── */}
      <div className={`mt-6 transition-opacity ${flipped ? "opacity-60" : ""}`}>
        <p className="text-center font-body text-[12px] font-semibold uppercase tracking-[0.2em] text-ink-muted">
          Say a sentence with this word
        </p>

        <div className="mt-4 flex flex-col items-center">
          <button
            type="button"
            onClick={recording ? stopRecording : startRecording}
            disabled={flipped || checking}
            aria-label={recording ? "Stop recording" : "Start recording"}
            className={`btn-3d grid h-16 w-16 place-items-center rounded-full border-[3px] disabled:pointer-events-none disabled:opacity-40 ${
              recording
                ? "animate-pulse border-flag-red-deep bg-white text-flag-red"
                : "border-flag-red-deep bg-flag-red text-white"
            }`}
            style={redShadow}
          >
            {recording ? (
              /* stop square */
              <svg viewBox="0 0 24 24" className="h-6 w-6" aria-hidden>
                <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" />
              </svg>
            ) : (
              /* microphone */
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.2}
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-7 w-7"
                aria-hidden
              >
                <rect x="9" y="2.5" width="6" height="11.5" rx="3" />
                <path d="M5 11.5a7 7 0 0 0 14 0" />
                <path d="M12 18.5v3" />
              </svg>
            )}
          </button>
          <p className="mt-3 font-body text-[12px] font-semibold uppercase tracking-[0.18em] text-ink-muted">
            {checking
              ? "Checking your sentence…"
              : recording
                ? `0:${String(elapsed).padStart(2, "0")} · tap to stop`
                : "Tap to record"}
          </p>
        </div>

        {attemptError && (
          <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-red-deep">
            {attemptError}
          </p>
        )}

        {card.example && (
          <div className="mt-3 text-center">
            <button
              type="button"
              onClick={() => {
                setRevealed(true);
                setFlipped(true);
              }}
              disabled={flipped || busy}
              className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red disabled:opacity-40"
            >
              Reveal example
            </button>
          </div>
        )}
      </div>

      {/* Quietly parked at the bottom: prune the pool mid-practice. Deletes
          only the user_cards link — the pack reverts to "Add the rest". */}
      <div className="mt-6 text-center">
        <button
          type="button"
          onClick={handleRemove}
          disabled={removing || busy}
          className="font-body text-[11px] font-bold uppercase tracking-[0.2em] text-ink-faint transition-colors hover:text-flag-red disabled:opacity-40"
        >
          {removing ? "Removing…" : "✕ Remove this word from my pool"}
        </button>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
        {label}
      </dt>
      <dd className="mt-0.5 font-body text-[16px] leading-relaxed text-ink">
        {value}
      </dd>
    </div>
  );
}
