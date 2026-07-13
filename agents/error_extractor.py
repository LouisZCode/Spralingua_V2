"""Post-session grammar-error extractor (GRAM-001 Phase 2, Harvester B).

The situation-drill counterpart to Satzschmiede's inline harvester
(``satz/examiner.py``): where the examiner classifies one spoken sentence,
this makes ONE structured-output pass over a whole stored drill transcript
and returns the learner's classifiable grammar slips, each mapped to a fixed
``grammar/taxonomy.yaml`` pattern id.

Same Cerebras ``gpt-oss-120b`` wiring as ``agents/evaluator.py`` — a single
non-streaming judgement fired on disconnect, wrapped non-fatally by
``pipeline/factory.py``. The catalog is injected into the prompt via
``taxonomy_brief()`` and the returned slugs are validated against
``load_taxonomy()``, so a hallucinated id can never reach the ledger (D3:
unclassifiable errors are simply dropped — no ``unclassified`` bucket).

Ledger grain is **one entry per distinct pattern per session**, not per raw
slip: the ledger tracks patterns, not individual mistakes, so repeating the
same error three times in one conversation is one occurrence with the
clearest example kept. Deduplication is enforced both in the prompt and
defensively here.

Template substitution uses ``str.replace`` (not ``str.format``) for the same
reason as the evaluator: the prompt body carries literal JSON braces.
"""

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from config import openrouter_api_key, openrouter_base_url
from grammar import load_taxonomy, taxonomy_brief

EXTRACTOR_MODEL = "openai/gpt-oss-120b"


class ExtractedError(BaseModel):
    pattern_id: str = Field(
        description="The taxonomy id of the broken rule, copied verbatim from the catalog"
    )
    sentence: str = Field(
        description="The learner's own erroneous German sentence, quoted from one of their turns"
    )
    corrected: str = Field(
        description="That sentence minimally repaired in German — keep the learner's idea and words, change only what is wrong"
    )
    note: str = Field(
        description="One short English line (AT MOST 10 words) naming the broken rule; never restate the fix"
    )


class ErrorExtraction(BaseModel):
    errors: list[ExtractedError] = Field(
        description="One entry per DISTINCT broken pattern; empty when the learner made no classifiable grammar errors"
    )


PROMPT = """# Role
You harvest German grammar errors from one language-learning conversation, for a private error ledger the learner never sees during the session. You do NOT grade, praise, or correct the learner here — you only classify.

# What you get
- taxonomy: a fixed catalog of German grammar-error patterns, one per line as `id — label ("wrong" → "right")`.
- transcript: the conversation in chat format. `User:` lines are the LEARNER; `Bot:` lines are the native-speaking partner.

# What to classify
- Look ONLY at the `User:` lines. The `Bot:` partner is a native speaker — never classify their German.
- Find grammar mistakes the learner made **in German**, and map each to the ONE catalog pattern whose wrong→right pair matches it. Use ids from the catalog only — never invent one.
- A pattern counts once. If the learner breaks the same rule several times, emit ONE entry for it and quote the single clearest example.

# What to skip
- Anything that matches no catalog pattern — drop it silently (no bucket for the unclassifiable).
- Purely lexical slips: a misremembered noun gender, a wrong word choice, a vocabulary gap. Those are not grammar patterns.
- Turns that are not German (e.g. the learner answered in English) — there is no German grammar to classify there.
- A perfectly correct conversation yields an empty list. Do not manufacture errors to fill it.

# Output
Respond with ONLY valid JSON, no surrounding text, no code fences, no comments:
{
  "errors": [
    {
      "pattern_id": "<a catalog id, verbatim>",
      "sentence": "<the learner's own German sentence containing the error>",
      "corrected": "<the same sentence minimally repaired in German>",
      "note": "<AT MOST 10 English words naming the broken rule; do not restate the fix>"
    }
  ]
}

One entry per distinct pattern. Empty "errors" array when there is nothing to classify.

# Example
taxonomy (excerpt):
- nebensatz-verbende — weil/dass/wenn send the verb to the end ("weil ich bin müde" → "weil ich müde bin")
- perfekt-aux-sein — Perfekt with sein for movement/change ("ich habe gegangen" → "ich bin gegangen")
- akkusativ-artikel — Accusative article for direct objects ("ich sehe der Mann" → "ich sehe den Mann")

transcript:
Bot: Guten Tag! Wie geht es Ihnen?
User: Gut, danke. Ich bleibe heute zu Hause, weil ich bin krank.
Bot: Oh, das tut mir leid. Waren Sie schon beim Arzt?
User: Ja, gestern. Ich habe zum Arzt gegangen und habe der Termin verpasst.

{
  "errors": [
    {
      "pattern_id": "nebensatz-verbende",
      "sentence": "Ich bleibe heute zu Hause, weil ich bin krank.",
      "corrected": "Ich bleibe heute zu Hause, weil ich krank bin.",
      "note": "'weil' sends the verb to the end"
    },
    {
      "pattern_id": "perfekt-aux-sein",
      "sentence": "Ich habe zum Arzt gegangen.",
      "corrected": "Ich bin zum Arzt gegangen.",
      "note": "'gehen' is movement — Perfekt takes 'sein'"
    },
    {
      "pattern_id": "akkusativ-artikel",
      "sentence": "Ich habe der Termin verpasst.",
      "corrected": "Ich habe den Termin verpasst.",
      "note": "direct object takes the accusative article"
    }
  ]
}
("bleibe" misspelt as "bleibe/bleibe" is speech-recognition noise, not a pattern; the native Bot turns were ignored.)

# Now classify
taxonomy:
{taxonomy}

transcript:
{transcript}
"""


async def extract_errors(
    *, transcript: str, session_id: str | None = None
) -> ErrorExtraction:
    """Classify the learner's grammar slips in a drill transcript.

    Returns an :class:`ErrorExtraction` whose ``errors`` are guaranteed to
    carry valid catalog ids, deduplicated by ``pattern_id`` (first-seen
    wins). Callers upsert each entry into the ledger with ``source="situation"``.

    ``session_id`` (OBS-006) files the harvest's Langfuse trace into the same
    Session as the conversation turns it classifies.
    """
    rendered = (
        PROMPT
        .replace("{taxonomy}", taxonomy_brief())
        .replace("{transcript}", transcript)
    )
    llm = ChatOpenAI(
        model=EXTRACTOR_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        timeout=30,
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": True}},
    ).with_structured_output(ErrorExtraction, include_raw=True)
    with generation_span(
        "grammar-harvest",
        model=EXTRACTOR_MODEL,
        input_text=transcript,
        session_id=session_id,
    ) as span:
        result, usage = unwrap_structured_output(await llm.ainvoke(rendered))
        record_generation_output(span, result.model_dump_json(), usage)

    # The ledger keys on catalog ids — drop hallucinated slugs (same guard as
    # the examiner) and collapse duplicate patterns to one entry (first wins),
    # so one session bumps a pattern's occurrences by exactly one.
    catalog = load_taxonomy()
    seen: set[str] = set()
    deduped: list[ExtractedError] = []
    for err in result.errors:
        if err.pattern_id not in catalog or err.pattern_id in seen:
            continue
        seen.add(err.pattern_id)
        deduped.append(err)
    result.errors = deduped
    return result
