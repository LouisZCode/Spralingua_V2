"""LLM word enricher for Satzschmiede (SATZ-002, Phase 2: add your own word).

Turns a learner-typed German word into a full flashcard following the same
card rules the curated YAML validator enforces (``satz/content.py``): nouns
carry article + gender/plural note, verbs are bare infinitives with no note
(the Verbs Rule), phrases carry a register note.

Same Cerebras ``gpt-oss-120b`` wiring as ``agents/evaluator.py`` — one
structured-output call, no streaming. The route only fires this on a
canonical-catalog miss, so a word is only ever enriched once; every later
learner who adds it gets the existing card for free. Adjectives carry a
comparison note (comparative · superlative) — the forms are the card's
hidden grammar, and irregulars (gut/besser) get flagged for free.

SATZ-023: adverbs (leider, oft, gern, deshalb, eher, insbesondere, …) are
their own type — before this they had nowhere to go: rejected ("we don't
make cards for adverbs yet"), misfiled as ``phrase``, or misfiled as
``adjective`` with an invented comparative note. An adverb card's note is
optional and only ever states a real fact (its kind/position, or a genuine
comparison when one exists) — never an invented one.
"""

from typing import Literal, Optional

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)

ENRICHER_MODEL = "openai/gpt-oss-120b"


class EnrichedCard(BaseModel):
    valid: bool = Field(
        description="True iff the input is a real German word or a short common German phrase"
    )
    reason: Optional[str] = Field(
        default=None,
        description=(
            "Only when valid=false: one short, friendly English sentence telling the "
            "learner why the input was rejected (shown to them verbatim)"
        ),
    )
    source_language: Optional[str] = Field(
        default=None,
        description=(
            "Only when the input is a real word/short phrase in ANOTHER language "
            "that has a clear German equivalent: that language, in English "
            "(e.g. 'English', 'Spanish'). Null otherwise"
        ),
    )
    german_equivalent: Optional[str] = Field(
        default=None,
        description=(
            "Set together with source_language: the German word to offer instead, "
            "written naturally (a noun WITH its article, e.g. 'der Termin'; other "
            "types bare). The learner confirms before we build its card. Null otherwise"
        ),
    )
    type: Optional[Literal["noun", "verb", "phrase", "adjective", "preposition", "adverb"]] = Field(
        default=None, description="Card type; null when valid=false"
    )
    target: Optional[str] = Field(
        default=None,
        description="The normalized base form to practice (see the card rules)",
    )
    article: Optional[Literal["der", "die", "das"]] = Field(
        default=None, description="Definite article — nouns only, null otherwise"
    )
    reflexive: bool = Field(
        default=False, description="True only for genuinely reflexive verbs"
    )
    gloss: Optional[str] = Field(
        default=None, description="Concise English meaning, one sense only"
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Nouns: gender·plural note. Phrases: register hint. Adjectives: "
            "comparison note. Prepositions: the case it governs. Adverbs: "
            "OPTIONAL — its kind and where it sits (e.g. 'sentence adverb "
            "· usually first or after the verb'), or a genuine comparison "
            "when one exists (gern -> 'comparison: lieber · am liebsten'); "
            "null when there's nothing worth noting, and NEVER an invented "
            "comparison. Verbs: MUST be null"
        ),
    )
    past_form: Optional[str] = Field(
        default=None,
        description=(
            "Verbs only, null otherwise: the past form as Germans actually "
            "SPEAK it (see the verb past rules)"
        ),
    )
    past_example: Optional[str] = Field(
        default=None,
        description=(
            "Verbs only, null otherwise: one natural German sentence using "
            "the verb in that spoken past"
        ),
    )
    example: Optional[str] = Field(
        default=None, description="One natural German sentence using the word"
    )
    level: Optional[Literal["A1", "A2", "B1", "B2", "C1", "C2"]] = Field(
        default=None, description="CEFR difficulty estimate"
    )


PROMPT = """# Role
You build one flashcard for a German vocabulary trainer ("Satzschmiede"). A learner typed something they want to practice. Decide whether it is usable, then forge the card.

# Accept / reject / translate — three outcomes
- ACCEPT any real German word or short common phrase (idioms, collocations, polite formulas). Established loanwords used in everyday German (das Handy, der Laptop) count as German. Set valid=true and forge the card below.
- TRANSLATE when the input is a real word or short phrase in ANOTHER language (English, Spanish, …) that has a clear, single German equivalent. Set valid=false, but fill `source_language` (that language, in English — e.g. "English"), `german_equivalent` (the German word to offer — a noun WITH its article like "der Termin", other types bare), and `gloss` (the concise English meaning of that German word, one sense). Also write a friendly one-line `reason`. Leave the card-building fields (type, target, article, note, …) null — the learner confirms first, then we build the card properly.
- REJECT gibberish, bare proper names, full sentences (more than ~6 words), and foreign words with NO clean German equivalent. Set valid=false, write ONE short, friendly English sentence in `reason`, and leave every other field null (no german_equivalent).

# Normalize the input first
- Strip a leading article (der/die/das/ein/eine) — it tells you the noun's gender but never belongs in `target`.
- Strip a leading "sich" — it tells you the verb is reflexive (set reflexive=true) but never belongs in `target`.
- Fix casing: German nouns are capitalized; verbs, adjectives, prepositions, adverbs and phrases are lowercase.
- If the learner typed an inflected form (plural noun, conjugated verb), build the card for the base form (singular noun, infinitive verb).

# Card rules (strict)
- type "noun": `target` = bare capitalized singular noun. `article` = der/die/das. `note` = "<masculine|feminine|neuter> · plural: die <plural form>" — or "<gender> · no plural" if it has none.
- type "verb": `target` = bare infinitive, WITHOUT "sich" and without any preposition or case hint. `reflexive` = true only for genuinely reflexive verbs. `note` MUST be null — the learner has to recall case/preposition/reflexivity unaided.
- type "verb" ALSO fills the past fields (every other type leaves them null):
  - `past_form`: the past as Germans actually SPEAK it, third person singular. Default is the Perfekt with its auxiliary: "ist geflogen", "hat gedacht". When the Präteritum is also everyday speech for this verb (denken → dachte, wissen → wusste, geben → gab, finden → fand), give both spoken-first: "dachte · hat gedacht". When the Perfekt is unnatural in speech (sein, haben, werden, the modals), give ONLY the Präteritum: "war", "hatte", "wollte". Reflexive verbs include the pronoun: "hat sich gefreut".
  - `past_example`: one natural German sentence using the verb in that spoken past.
- type "preposition": `target` = the preposition, lowercase, bare. `note` = the case it forces on its object, written as a short tag: "+ accusative" (durch, für, gegen, ohne, um), "+ dative" (aus, bei, mit, nach, seit, von, zu, gegenüber), or "+ genitive" (wegen, trotz, während, (an)statt). For a TWO-WAY preposition (an, auf, in, über, unter, vor, hinter, neben, zwischen): `note` = "two-way · accusative (Wohin?) · dative (Wo?)". `gloss` = its core spatial or logical meaning. Also use this type for a word that works as a prepositional adverb (e.g. gegenüber) — pick its prepositional case.
- type "adverb": ONLY for words that never take an ending — sentence adverbs (leider, wahrscheinlich, deshalb, trotzdem), temporal/frequency (oft, immer, bald, manchmal), degree (sehr, ziemlich, fast, eher), focus (sogar, insbesondere, nur), modal particles (doch, mal, ja, halt). `target` = bare, lowercase. `note` = the one thing worth knowing — its kind and where it sits, in AT MOST 8 words (e.g. "sentence adverb · first or after the verb", "modal particle · mid-field only", "degree adverb · before the adjective") — or null when there's nothing worth flagging. Comparison ONLY when it genuinely exists: gern → "comparison: lieber · am liebsten", oft → "comparison: öfter · am häufigsten", bald → "comparison: eher · am ehesten"; otherwise NO comparison, and NEVER invent one. A word that ALSO declines attributively before a noun ("ein schnelles Auto") stays type "adjective" even when it's used adverbially too — e.g. schnell ("Er läuft schnell") stays "adjective", not "adverb". A prepositional adverb (gegenüber) stays "preposition" per the rule above. Worked examples: "leider" → adverb, note "sentence adverb · usually first or after the verb"; "schnell" → adjective (not adverb), note "comparative: schneller · superlative: am schnellsten" — it takes an ending before a noun, an adverb never does.
- type "phrase": `target` = the phrase as naturally written. `note` = one short register/usage hint (e.g. "polite register · when ordering").
- type "adjective": `target` = the base (positive) form, lowercase. `note` = "comparative: <form> · superlative: am <form>" (e.g. "comparative: schneller · superlative: am schnellsten") — watch for irregulars (gut → besser → am besten, hoch → höher, teuer → teurer). For absolute adjectives that aren't normally compared (schwanger, tot): `note` = "not usually compared".
- `gloss`: concise English meaning, ONE sense only — the most common everyday sense. If the word is hopelessly ambiguous without context, pick the dominant sense.
- `example`: one natural German sentence using the word — nouns appear with their article, reflexive verbs with their pronoun.
- `level`: CEFR estimate (A1–C2).

# Input
The learner typed: {word}
"""

# SATZ-021: appended (never blended into PROMPT itself) when a previous
# attempt for this same word failed satz/verifier.py's fact-check — without
# this, a "retry" is just resampling the same call and reliably reproduces
# the same error. Kept as its own block so the base PROMPT stays byte-
# identical when feedback is absent (the default).
FEEDBACK_BLOCK = """

# Your previous attempt was rejected
A fact-checker reviewed your last card for this same word and rejected it for this specific reason:
"{feedback}"
Fix EXACTLY that problem. Keep every other field the same if it was already correct — do not introduce a new error while fixing this one.
"""


def _normalize(e: EnrichedCard) -> EnrichedCard:
    """Defensive post-pass: enforce the card rules even when the model drifts
    (a stray leading article/"sich", a note on a verb) instead of failing the
    request over something we can trivially fix ourselves."""
    if not e.valid or not e.target or not e.type:
        return e
    t = " ".join(e.target.split())
    low = t.lower()
    if e.type == "noun":
        for lead in ("der ", "die ", "das "):
            if low.startswith(lead) and len(t) > len(lead):
                t = t[len(lead):]
                break
        t = t[0].upper() + t[1:]
    if e.type == "verb":
        if low.startswith("sich ") and len(t) > 5:
            t = t[5:]
            e.reflexive = True
        e.note = None  # the Verbs Rule, non-negotiable
    if e.type in ("preposition", "adverb"):
        t = t.lower()  # prepositions and adverbs are always lowercase
    if e.type != "noun":
        e.article = None
    if e.type != "verb":
        e.reflexive = False
        e.past_form = None
        e.past_example = None
    # A valid German card never carries a translation suggestion — those ride
    # only on the foreign-word reject path (valid=false, which returns above).
    e.source_language = None
    e.german_equivalent = None
    e.target = t
    return e


async def enrich_word(
    word: str,
    user_id: str | None = None,
    *,
    session_id: str | None = None,
    feedback: str | None = None,
) -> EnrichedCard:
    """One structured-output judgement call: reject or forge a rule-conformant card.

    ``session_id`` (OBS-008), when set, stamps ``langfuse.session.id`` on the
    ``satz-forge`` span so an add-a-word call fired mid-conversation (e.g.
    from a drill route) files into that conversation's Langfuse session
    instead of standing alone.

    ``feedback`` (SATZ-021), when set, is the ``problem`` from a rejected
    ``satz/verifier.py::verify_card`` verdict on this exact word's previous
    attempt — appended to the prompt so a retry actually fixes the named
    issue instead of resampling and reproducing it. ``None`` (the default)
    leaves the prompt byte-identical to before this parameter existed.
    """
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    # JUDGE-001 (2026-08-15): this generates card content (example sentence,
    # note), not a verdict — temperature=None keeps provider-default
    # sampling so re-enriching doesn't produce the identical card every time.
    llm = structured_judge_llm(EnrichedCard, temperature=None)
    prompt = PROMPT.replace("{word}", word)
    if feedback:
        prompt += FEEDBACK_BLOCK.replace("{feedback}", feedback)
    # OBS-006: the forge is its own single-generation Langfuse trace — the
    # add-a-word path's latency and verdicts were previously invisible.
    with generation_span(
        "satz-forge",
        model=ENRICHER_MODEL,
        input_text=word,
        user_id=user_id,
        session_id=session_id,
        environment="forge",
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return _normalize(result)
