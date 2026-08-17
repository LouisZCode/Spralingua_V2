// A single practice item — the contract GET /satz/deck serves (backend:
// satz/routes.py::_card_payload). Optional fields are omitted (not null) so
// `card.note && …` guards and `??` fallbacks stay clean.
export type CardType = "noun" | "verb" | "phrase" | "adjective" | "preposition" | "adverb";

export type Card = {
  id: string;
  type: CardType;
  target: string; // the clue on the front — bare noun (no article), bare verb (no preposition/case/reflexive), base adjective, bare preposition, bare adverb, or phrase
  article?: string; // nouns only: der/die/das — hidden on the clue, shown on the answer
  reflexive?: boolean; // reflexive verbs: `sich` is hidden on the clue and shown on the answer — the learner must spot the reflexivity unaided; the examiner must verify the sentence actually uses a reflexive pronoun
  gloss: string; // English meaning
  note?: string; // grammar strip: gender/plural, comparison forms, register, the case a preposition governs, or an adverb's kind/position (adverbs: optional, may be absent)
  tense?: "past"; // verb tense siblings: the spoken-past card (absent = present/base)
  tenseForm?: string; // the answer the past card reveals: "ist geflogen", "dachte · hat gedacht"
  example?: string; // optional model sentence, revealable as a hint
  // SATZ-017: rotation pool (original example first, forged leveled ones
  // after) — sent only when the backend has >1 for this card. Verbformen's
  // payload never sends it; the trainer falls back to `example`.
  examples?: string[];
  level?: string; // CEFR hint (A1–B2)
};

// Per-user schedule state riding on each deck card (backend: _srs_payload).
// `status` is computed server-side at fetch time so the client never compares
// clocks: "new" = never practiced, "due" = practice today, "later" =
// scheduled ahead. It's a snapshot — attempts made this session don't
// refresh it, so the trainer tracks session progress itself.
export type CardSrs = {
  status: "new" | "due" | "later";
  dueAt: string | null; // ISO timestamp; null while status is "new"
  intervalDays: number | null;
  reps: number;
};

export type DeckCard = Card & { srs: CardSrs };
