// A single practice item — the contract GET /satz/deck serves (backend:
// satz/routes.py::_card_payload). Optional fields are omitted (not null) so
// `card.note && …` guards and `??` fallbacks stay clean.
export type CardType = "noun" | "verb" | "phrase";

export type Card = {
  id: string;
  type: CardType;
  target: string; // the clue on the front — bare noun (no article), bare verb (no preposition/case/reflexive), or phrase
  article?: string; // nouns only: der/die/das — hidden on the clue, shown on the answer
  reflexive?: boolean; // reflexive verbs: `sich` is hidden on the clue and shown on the answer — the learner must spot the reflexivity unaided; the examiner must verify the sentence actually uses a reflexive pronoun
  gloss: string; // English meaning
  note?: string; // grammar strip: gender/plural, governed case, or register
  example?: string; // optional model sentence, revealable as a hint
  level?: string; // CEFR hint (A1–B2)
};
