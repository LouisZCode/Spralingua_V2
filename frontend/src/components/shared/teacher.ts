// AGENT-001: the teacher's frontend identity — mirrors shared/tandem.ts's
// role as the single source for "which lesson is the teacher" and her voice.
// The voice must be a key in services/tts.py::VOICE_MAP; Calm_Woman is a
// MiniMax preset suited to Clara's English-language explanations (the two
// cloned German voices already belong to Lena and the demo concierge).

export const TEACHER_LESSON = "teacher";
export const TEACHER_VOICE = "calm_woman";
export const TEACHER_NAME = "Clara";
