// TAND-008: the tandem partner registry — the frontend's single source for
// "which lessons are tandem" and each partner's display identity. Adding a
// partner = one entry here + a `{lessonId}.yaml` / `{lessonId}_topics.yaml`
// pair on the backend.
//
// Voices must be keys in services/tts.py::VOICE_MAP. Paul ships with the
// German-Male preset for now — a proper professional male voice gets
// auditioned later and swapped here (one constant).

export type PartnerId = "lena" | "paul";

export type TandemPartner = {
  id: PartnerId;
  lessonId: string;
  name: string;
  voice: string;
  // Picker-card copy: one-line register descriptor + a short bio.
  vibe: string;
  bio: string;
};

export const PARTNERS: Record<PartnerId, TandemPartner> = {
  lena: {
    id: "lena",
    lessonId: "tandem",
    name: "Lena",
    voice: "German_Female",
    vibe: "Everyday German · your friend from Leipzig",
    bio: "Bookshop, bike rides, whatever's going on in life — relaxed everyday chat with a friend who remembers your last talks.",
  },
  paul: {
    id: "paul",
    lessonId: "tandem_paul",
    name: "Paul",
    voice: "German-Male",
    vibe: "Office German · project lead from Stuttgart",
    bio: "Projects, deadlines, office life — the German real workplaces run on, over a friendly du.",
  },
};

// Every tandem lesson id — for call sites that branch on "is this a tandem
// session" (ConversationView's debrief modal + prominent Finish button).
export const TANDEM_LESSONS = new Set(
  Object.values(PARTNERS).map((p) => p.lessonId),
);

export function partnerByLesson(lessonId: string): TandemPartner | null {
  return Object.values(PARTNERS).find((p) => p.lessonId === lessonId) ?? null;
}
