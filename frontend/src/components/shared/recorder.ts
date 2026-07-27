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
  // Ref mirror so `stop`'s onstop handler always calls the latest callback
  // without needing it in `start`'s dependency array. Same "latest ref" idiom
  // as VocabNudge.tsx's fetchRef / GenusTrainer's submitDropRef — safe because
  // `current` is only ever read inside an event handler, never during render.
  const onStopRef = useRef(onStop);
  // eslint-disable-next-line react-hooks/refs -- latest-ref idiom, see above
  onStopRef.current = onStop;

  const stop = useCallback(() => {
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
      if (timerRef.current) clearInterval(timerRef.current);
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
    };
  }, []);

  const start = useCallback(async () => {
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
      setRecording(true);
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      setError("We need the microphone for this one — check the browser permission.");
    }
  }, []);

  return { recording, elapsed, error, start, stop, cancel };
}
