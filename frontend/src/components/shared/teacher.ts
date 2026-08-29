// AGENT-001: the teacher's frontend identity — mirrors shared/tandem.ts's
// role as the single source for "which lesson is the teacher" and her voice.
// The voice must be a key in services/tts.py::VOICE_MAP. Since 2026-08-29
// Clara trials the German_SweetLady MiniMax preset (checking its English +
// German quality); her previous voice, "calm_woman" (Calm_Woman), stays in
// VOICE_MAP so switching back is a one-line revert here. (The two cloned
// German voices already belong to Lena and the demo concierge.)

export const TEACHER_LESSON = "teacher";
export const TEACHER_VOICE = "german_sweet_lady";
export const TEACHER_NAME = "Clara";
