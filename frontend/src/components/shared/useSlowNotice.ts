"use client";

import { useEffect, useState } from "react";
import { SLOW_TRANSCRIPTION_THRESHOLD_MS } from "./copy";

// STT-004 P2: `true` once `active` has stayed `true` for longer than
// `thresholdMs` — the shared timer behind the "transcription is slow" line
// every recorder that awaits an attempt's audio upload shows instead of a
// silent long spinner. One hook, not a setTimeout/clearTimeout pair copied
// into every trainer (the same "one hook, not five copies" convention
// recorder.ts's own MediaRecorder wiring already follows).
//
// Resets to `false` the moment `active` goes false, whether that's a normal
// landed response or an error — callers don't need to reset it themselves.
export function useSlowNotice(
  active: boolean,
  thresholdMs: number = SLOW_TRANSCRIPTION_THRESHOLD_MS
): boolean {
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(() => setSlow(true), thresholdMs);
    // Cleanup (fires on unmount AND on every re-run right before it, i.e.
    // the moment `active` flips back to false) is what resets `slow` —
    // never call setState synchronously in the effect body itself.
    return () => {
      clearTimeout(timer);
      setSlow(false);
    };
  }, [active, thresholdMs]);

  return slow;
}
