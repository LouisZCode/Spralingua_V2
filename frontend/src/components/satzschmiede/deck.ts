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

// ── Placeholder verdict helper ──────────────────────────────────────────────
// Until the real examiner (backend LLM, POST /satz/attempts) lands, "Check
// answer" does a dumb contains-test against stems derived from the target.
// This whole block is disposable — it goes when the examiner phase ships.

// Separable prefixes, longest first so "auseinander" wins over "aus". When a
// verb separates ("Sie nimmt … teil"), the prefix — not the stem — is what
// shows up in the sentence, so it counts as a match too.
const SEPARABLE_PREFIXES = [
  "auseinander",
  "gegenüber",
  "zusammen",
  "zurück",
  "herunter",
  "hinunter",
  "heraus",
  "hinaus",
  "herein",
  "hinein",
  "vorbei",
  "weiter",
  "wieder",
  "statt",
  "teil",
  "mit",
  "nach",
  "fest",
  "frei",
  "los",
  "weg",
  "ab",
  "an",
  "auf",
  "aus",
  "bei",
  "ein",
  "vor",
  "zu",
];

function normalize(word: string): string {
  return word.toLowerCase().replace(/[^a-zäöüß]/g, "");
}

export function targetStems(card: Card): string[] {
  // Phrases: their longest word is the most distinctive one ("darauf",
  // "hätte", "mitnehmen") — good enough for a placeholder.
  const word =
    card.type === "phrase"
      ? card.target
          .split(/\s+/)
          .reduce((a, b) => (normalize(b).length > normalize(a).length ? b : a), "")
      : card.target;
  const w = normalize(word);
  if (card.type !== "verb") {
    return [w];
  }
  // Verbs: drop the infinitive ending so conjugated forms match
  // ("freuen" → "freu" matches "freue mich").
  const stems = [w.replace(/e?n$/, "")];
  const prefix = SEPARABLE_PREFIXES.find(
    (p) => w.startsWith(p) && w.length > p.length + 2
  );
  if (prefix) {
    stems.push(prefix);
  }
  return stems;
}
