// A single practice item. This type is the contract the backend will satisfy
// later (from a content file or the DB); for now MOCK_DECK below fills it so we
// can design the card + answer UI entirely on the frontend, with no backend.
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
};

// Six sample items — two nouns, two verbs, two phrases — picked to exercise all
// three card types (and their grammar strips). Placeholder content only; swap
// for a real deck once the backend serves one.
export const MOCK_DECK: Card[] = [
  {
    id: "n-rechnung",
    type: "noun",
    target: "Rechnung",
    article: "die",
    gloss: "the bill / invoice",
    note: "feminine · plural: die Rechnungen",
    example: "Können wir bitte die Rechnung haben?",
  },
  {
    id: "n-termin",
    type: "noun",
    target: "Termin",
    article: "der",
    gloss: "the appointment",
    note: "masculine · plural: die Termine",
    example: "Ich habe morgen einen Termin beim Arzt.",
  },
  {
    id: "v-freuen-auf",
    type: "verb",
    target: "freuen",
    reflexive: true,
    gloss: "to look forward to",
    example: "Ich freue mich auf das Wochenende.",
  },
  {
    id: "v-teilnehmen-an",
    type: "verb",
    target: "teilnehmen",
    gloss: "to take part in",
    example: "Sie nimmt an dem Kurs teil.",
  },
  {
    id: "p-kommt-darauf-an",
    type: "phrase",
    target: "Das kommt darauf an.",
    gloss: '"That depends."',
    note: "spoken · neutral register",
    example: '„Kommst du mit?" – „Das kommt darauf an."',
  },
  {
    id: "p-haette-gern",
    type: "phrase",
    target: "Ich hätte gern …",
    gloss: '"I’d like …"',
    note: "polite · ordering & requests",
    example: "Ich hätte gern einen Kaffee, bitte.",
  },
];

// Mock-only: the keyword the placeholder "Check" looks for in the learner's
// sentence to fake a correct/close verdict. Purely to make the frontend flow
// tangible for now — the real examiner (backend) replaces all of this.
// Reflexive cards (reflexive: true) ALSO require a reflexive pronoun on top of
// this keyword — that extra check lives in VocabTrainer.
export const MOCK_KEYWORDS: Record<string, string> = {
  "n-rechnung": "rechnung",
  "n-termin": "termin",
  "v-freuen-auf": "freu",
  "v-teilnehmen-an": "teil",
  "p-kommt-darauf-an": "darauf an",
  "p-haette-gern": "gern",
};
