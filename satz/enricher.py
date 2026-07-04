"""LLM word enricher for Satzschmiede (SATZ-002, Phase 2: add your own word).

Turns a learner-typed German word into a full flashcard following the same
card rules the curated YAML validator enforces (``satz/content.py``): nouns
carry article + gender/plural note, verbs are bare infinitives with no note
(the Verbs Rule), phrases carry a register note.

Same Cerebras ``gpt-oss-120b`` wiring as ``agents/evaluator.py`` — one
structured-output call, no streaming. The route only fires this on a
canonical-catalog miss, so a word is only ever enriched once; every later
learner who adds it gets the existing card for free.
"""

from typing import Literal, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config import openrouter_api_key, openrouter_base_url

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
    type: Optional[Literal["noun", "verb", "phrase"]] = Field(
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
        description="Nouns: gender·plural note. Phrases: register hint. Verbs: MUST be null",
    )
    example: Optional[str] = Field(
        default=None, description="One natural German sentence using the word"
    )
    level: Optional[Literal["A1", "A2", "B1", "B2", "C1", "C2"]] = Field(
        default=None, description="CEFR difficulty estimate"
    )


PROMPT = """# Role
You build one flashcard for a German vocabulary trainer ("Satzschmiede"). A learner typed something they want to practice. Decide whether it is usable, then forge the card.

# Accept / reject
- ACCEPT any real German word or short common phrase (idioms, collocations, polite formulas). Established loanwords used in everyday German (das Handy, der Laptop) count as German.
- REJECT gibberish, words from other languages, bare proper names, and full sentences (more than ~6 words). When rejecting, set valid=false and write ONE short, friendly English sentence in `reason` telling the learner why — suggest the German equivalent if you know it (e.g. "That's English — the German word for it is 'Termin', try that!"). Leave every other field null.

# Normalize the input first
- Strip a leading article (der/die/das/ein/eine) — it tells you the noun's gender but never belongs in `target`.
- Strip a leading "sich" — it tells you the verb is reflexive (set reflexive=true) but never belongs in `target`.
- Fix casing: German nouns are capitalized; verbs and phrases are lowercase.
- If the learner typed an inflected form (plural noun, conjugated verb), build the card for the base form (singular noun, infinitive verb).

# Card rules (strict)
- type "noun": `target` = bare capitalized singular noun. `article` = der/die/das. `note` = "<masculine|feminine|neuter> · plural: die <plural form>" — or "<gender> · no plural" if it has none.
- type "verb": `target` = bare infinitive, WITHOUT "sich" and without any preposition or case hint. `reflexive` = true only for genuinely reflexive verbs. `note` MUST be null — the learner has to recall case/preposition/reflexivity unaided.
- type "phrase": `target` = the phrase as naturally written. `note` = one short register/usage hint (e.g. "polite register · when ordering").
- `gloss`: concise English meaning, ONE sense only — the most common everyday sense. If the word is hopelessly ambiguous without context, pick the dominant sense.
- `example`: one natural German sentence using the word — nouns appear with their article, reflexive verbs with their pronoun.
- `level`: CEFR estimate (A1–C2).

# Input
The learner typed: {word}
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
    if e.type != "noun":
        e.article = None
    if e.type != "verb":
        e.reflexive = False
    e.target = t
    return e


async def enrich_word(word: str) -> EnrichedCard:
    """One structured-output judgement call: reject or forge a rule-conformant card."""
    llm = ChatOpenAI(
        model=ENRICHER_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": True}},
    ).with_structured_output(EnrichedCard)
    result = await llm.ainvoke(PROMPT.replace("{word}", word))
    return _normalize(result)
