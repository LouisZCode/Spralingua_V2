"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// Shared MediaRecorder wiring (TAND-003), lifted from the tap-record /
// tap-stop pattern proven in sprechen/SprechenTrainer.tsx: MIME fallbacks so
// Chrome/Firefox (opus-in-webm) and Safari (aac-in-mp4) both work, a hard
// recording-length cap so a forgotten mic doesn't grow forever, and
// mic-track cleanup on both normal stop and unmount.
//
// TODO: SprechenTrainer and szenario/SzenarioTrainer still keep their own
// (near-identical) copies of this logic — migrating them to this hook is a
// separate pass, not bundled into TAND-003. New recording consumers should
// use this hook instead of copy-pasting the pattern again.

const MAX_RECORD_SECONDS = 90;

// Chrome/Firefox record opus-in-webm, Safari aac-in-mp4 — Deepgram takes both.
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

// STT-006: Deepgram nova-3 can drop a leading unstressed word when speech
// starts at t≈0 of the clip (VERIFY-3 bisected it — 200-400ms of leading
// silence recovers the word, 0.5-3.4% of spoken attempts hit this). The
// recorder starts immediately as before; only the visible "recording" cue
// (and the elapsed timer) is held back this long, so the clip's opening
// frames are real captured silence before the learner is invited to speak.
const STT_LEAD_IN_MS = 300;

export interface UseRecorderResult {
  recording: boolean;
  elapsed: number;
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
  cancel: () => void;
}

/**
 * `onStop` fires once per completed recording with the finished clip — never
 * for a recording discarded via `cancel()` OR by unmounting mid-take. Callers
 * decide what "auto-send on stop" means (e.g. POST the blob).
 */
export function useRecorder(onStop: (blob: Blob) => void): UseRecorderResult {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  // Set when the clip must NOT be handed to onStop (unmount mid-recording).
  const discardRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // STT-006: the pending "flip recording on" timer, live only during the
  // lead-in window right after rec.start(). Any stop path must clear it so a
  // stop/discard/unmount that lands mid-lead-in never leaves a delayed
  // setRecording(true) to fire after the clip is already gone.
  const leadInTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Ref mirror so `stop`'s onstop handler always calls the latest callback
  // without needing it in `start`'s dependency array. Same "latest ref" idiom
  // as VocabNudge.tsx's fetchRef / GenusTrainer's submitDropRef — safe because
  // `current` is only ever read inside an event handler, never during render.
  const onStopRef = useRef(onStop);
  // eslint-disable-next-line react-hooks/refs -- latest-ref idiom, see above
  onStopRef.current = onStop;

  const stop = useCallback(() => {
    if (leadInTimerRef.current) {
      clearTimeout(leadInTimerRef.current);
      leadInTimerRef.current = null;
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
    }
    setRecording(false);
  }, []);

  // UI-006: a messed-up take — kill the clip without handing it to onStop;
  // the next start() resets discardRef, so the caller just records again.
  const cancel = useCallback(() => {
    discardRef.current = true;
    if (leadInTimerRef.current) {
      clearTimeout(leadInTimerRef.current);
      leadInTimerRef.current = null;
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
    }
    setRecording(false);
  }, []);

  // Hard cap — a forgotten mic auto-stops (and auto-sends, via onStop) at the limit.
  useEffect(() => {
    if (recording && elapsed >= MAX_RECORD_SECONDS) {
      stop();
    }
  }, [recording, elapsed, stop]);

  // Unmount mid-recording: kill the mic, never fire onStop for the partial clip.
  useEffect(() => {
    return () => {
      discardRef.current = true;
      if (leadInTimerRef.current) clearTimeout(leadInTimerRef.current);
      if (timerRef.current) clearInterval(timerRef.current);
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
    };
  }, []);

  const start = useCallback(async () => {
    // STT-006: a caller re-invoking start() during the lead-in window (e.g.
    // a stale "not recording yet" UI still wired to the start action) must
    // not spin up a second stream/recorder on top of the one already live.
    if (recorderRef.current && recorderRef.current.state !== "inactive") return;
    setError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("We need the microphone for this one — check the browser permission.");
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
        onStopRef.current(blob);
      };
      recorderRef.current = rec;
      rec.start();
      // STT-006: capture starts now; the "recording" cue (and its timer)
      // waits out the lead-in so the clip's opening frames are real silence.
      leadInTimerRef.current = setTimeout(() => {
        leadInTimerRef.current = null;
        setRecording(true);
        setElapsed(0);
        timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
      }, STT_LEAD_IN_MS);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      setError("We need the microphone for this one — check the browser permission.");
    }
  }, []);

  return { recording, elapsed, error, start, stop, cancel };
}
