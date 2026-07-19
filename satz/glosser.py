"""On-demand word gloss for the hover/tap-to-translate UI (UI-007).

Hover/tap any German word anywhere in the app -> translation + example, with
click-to-add into the vocabulary deck (the add itself reuses ``POST
/satz/cards`` — this module only builds the lookup). Same Cerebras
structured-output wiring as ``satz/explainer.py``: one structured-output
call, no streaming.

The route (``satz/routes.py::gloss_word_route``) checks the shared
Satzschmiede catalog and the ``word_glosses`` cache table before ever
reaching here — this module only runs on a genuine cache miss, and its
result gets cached so the same surface form never triggers a second call.
"""

from typing import Optional

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)

GLOSSER_MODEL = "openai/gpt-oss-120b"


class WordGlossResult(BaseModel):
    """One dictionary-form gloss for a word as used in a given sentence."""

    lemma: str = Field(
        description=(
            "Dictionary form: noun -> nominative singular (bare, no "
            "article); adjective -> base (positive) form; verb (incl. "
            "participles/finite forms) -> infinitive"
        )
    )
    article: Optional[str] = Field(
        default=None,
        description="der/die/das if lemma is a noun, else null",
    )
    gloss: str = Field(
        description=(
            "Concise English meaning of the lemma AS USED in the given "
            "sentence, 2-5 words, no sentence"
        )
    )
    example: str = Field(
        description=(
            "One NEW short A1/A2 German sentence using the lemma "
            "naturally — not the given sentence"
        )
    )


PROMPT = """# Role
You are a German dictionary lookup inside a language-learning app. The learner hovered or tapped ONE word inside a German sentence. Identify that word's dictionary form (lemma) and gloss it AS USED in that sentence.

# Rules
- Return the DICTIONARY FORM (lemma), not the inflected surface form:
  - noun -> nominative singular, bare (no article in `lemma` itself — put the article in `article`)
  - adjective -> base (positive) form
  - verb, including participles and any finite/conjugated form -> infinitive
- `article`: der/die/das when the lemma is a noun; null for every other word type.
- `gloss`: the concise English meaning of the lemma as it is used HERE, 2-5 words, no full sentence. Gloss the LEMMA itself (singular for nouns, infinitive for verbs), never the inflected surface form.
- `example`: one NEW short A1/A2-level German sentence using the lemma naturally. Do not reuse the given sentence.

# Examples
Sentence: "In diesen Mänteln ist es schön warm."
Word: "Mänteln" -> lemma "Mantel", article "der", gloss "coat", example "Der Mantel hängt an der Tür."

Sentence: "Er wurde von den anderen Kindern gehänselt."
Word: "gehänselt" -> lemma "hänseln", article null, gloss "to tease, to mock", example "Die Kinder hänseln ihn oft."

Sentence: "An einem kalten Tag bleiben wir lieber zu Hause."
Word: "kalten" -> lemma "kalt", article null, gloss "cold", example "Heute ist es sehr kalt."

# Input
Sentence: "{context}"
Word: "{word}"
"""


async def gloss_word(
    word: str,
    context: str,
    user_id: str | None = None,
    *,
    session_id: str | None = None,
) -> WordGlossResult:
    """One structured-output call: resolve `word`'s lemma + gloss + example
    as used inside `context`.

    ``session_id``, when set, stamps ``langfuse.session.id`` on the
    ``satz-gloss`` span so a hover fired mid-drill files into that
    practice-sitting's Langfuse session instead of standing alone.
    """
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    llm = structured_judge_llm(WordGlossResult)
    prompt = PROMPT.replace("{context}", context).replace("{word}", word)
    with generation_span(
        "satz-gloss",
        model=GLOSSER_MODEL,
        input_text=prompt,
        user_id=user_id,
        session_id=session_id,
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result
