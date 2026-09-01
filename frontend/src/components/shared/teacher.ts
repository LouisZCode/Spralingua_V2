// AGENT-001: the teacher's frontend identity — mirrors shared/tandem.ts's
// role as the single source for "which lesson is the teacher" and her voice.
// The voice must be a key in services/tts.py::VOICE_MAP. Since 2026-09-01
// Clara uses the MiniMax TTV voice-design id "clara_ttv". Her previous
// voices — "german_sweet_lady" (German_SweetLady, 2026-08-29 → 2026-09-01)
// and "calm_woman" (Calm_Woman, until 2026-08-29) — stay in VOICE_MAP so
// switching back is a one-line revert here. (The two cloned German voices
// already belong to Lena and the demo concierge.)

export const TEACHER_LESSON = "teacher";
export const TEACHER_VOICE = "clara_ttv";
export const TEACHER_NAME = "Clara";
