// UI-014: the one place load-failure copy lives. Every surface that fails a
// fetch renders loadError(<what it was loading>) — tone changes happen here.
export function loadError(what: string): string {
  return `Couldn't load ${what} — try again in a moment.`;
}

// STT-004 P2: how long an audio-upload/transcription wait stays a silent
// spinner before the "slow" notice appears (frontend/src/components/shared/
// useSlowNotice.ts). Mirrors the backend's own visibility threshold
// (satz/examiner.py::_SLOW_TRANSCRIBE_THRESHOLD_S = 4.0s) — kept as its own
// TS constant since the two sides can't literally share a value, only the
// number.
export const SLOW_TRANSCRIPTION_THRESHOLD_MS = 4000;

// STT-004 P2: the one line every recorder shows once a transcription wait
// crosses SLOW_TRANSCRIPTION_THRESHOLD_MS, instead of a silent long spinner.
// House style: no "backend"/"server" words.
export function slowTranscriptionNotice(): string {
  return "Transcription is slow right now — hang on.";
}

// UI-014: the guard every catch block uses before trusting an Error's
// .message. Two things must never reach a learner: our own api.ts wrapper's
// generic "<path> failed (<status>)" fallback (thrown when a non-2xx
// response carries no string `detail`), and a raw browser fetch failure —
// "Failed to fetch" (Chrome/Firefox), "Load failed" (Safari), "NetworkError
// when attempting to fetch resource." (older Firefox), "fetch failed"
// (Node/undici). Everything else is assumed to be a genuine server-supplied
// detail string (a judge's verdict-adjacent note, an expiry reason, …) and
// passes through verbatim.
const RAW_NETWORK_ERROR = /^(failed to fetch|load failed|fetch failed|networkerror)/i;
export function safeMessage(err: unknown, fallback: string): string {
  if (!(err instanceof Error) || !err.message) return fallback;
  if (err.message.includes("failed (")) return fallback;
  if (RAW_NETWORK_ERROR.test(err.message.trim())) return fallback;
  return err.message;
}
