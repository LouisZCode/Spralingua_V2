"""Tandem post-session debrief (TANDEM-001 / GRAM-001 Phase 4).

The tandem counterpart to the situation-drill harvester (``error_extractor.py``)
and the goal evaluator (``evaluator.py``). One structured-output pass over a
tandem transcript does triple duty:

1. **Target patterns** — for each grammar pattern the session steered toward
   (the ledger's top open patterns, handed in as ``focus``): did it actually
   come up in the learner's own German, and did they get it right? This is what
   drives the streak/retire lifecycle — a pattern retires once the learner
   spontaneously produces the structure correctly, the strongest acquisition
   evidence there is.
2. **New errors** — any OTHER grammar slips, classified into the fixed
   ``grammar/taxonomy.yaml`` catalog (target patterns excluded — those are
   handled in section 1), upserted into the ledger like the drill harvester.
3. **Session note** — a 2–3 line English memory (topic + facts the learner
   shared) that feeds the next session's long-term prompt layer via
   ``database.repository.load_tandem_notes``.

Same Cerebras ``gpt-oss-120b`` wiring as the other evaluators — a single
non-streaming judgement fired on disconnect, wrapped non-fatally by
``pipeline/factory.py``. Returned slugs are validated against
``load_taxonomy()`` so a hallucinated id can never reach the ledger, and the
two lists are kept disjoint (a target pattern is judged in ``patterns``, never
duplicated into ``new_errors``) so each pattern is applied to the ledger once.

``str.replace`` (not ``str.format``) for template substitution — the prompt
body carries literal JSON braces, same as the sibling evaluators.
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

DEBRIEF_MODEL = "openai/gpt-oss-120b"


class PatternOutcome(BaseModel):
    pattern_id: str = Field(
        description="One of the target pattern ids, copied verbatim from the list"
    )
    elicited: bool = Field(
        description="True if this structure actually came up in the LEARNER's own German this session"
    )
    produced_correctly: bool = Field(
        description="True if the learner used the structure correctly and spontaneously at least once; false if it never came up or every attempt was wrong"
    )
    evidence: str = Field(
        description="The learner's German sentence showing the structure — the correct one if produced_correctly, otherwise the clearest slip; 'none' if it never came up"
    )
    corrected: str = Field(
        description="If produced incorrectly, the minimal German repair of the evidence sentence; empty string otherwise"
    )
    note: str = Field(
        description="One short English line (AT MOST 10 words) on what happened with this structure"
    )


class NewError(BaseModel):
    pattern_id: str = Field(
        description="A taxonomy id that is NOT one of the target patterns, copied verbatim from the catalog"
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


class TandemDebrief(BaseModel):
    patterns: list[PatternOutcome] = Field(
        description="One entry per target pattern, in the same order given; empty when no targets were provided"
    )
    new_errors: list[NewError] = Field(
        description="Non-target grammar slips, one per DISTINCT pattern; empty when the rest was clean"
    )
    session_note: str = Field(
        description="2-3 short English lines: today's topic and any personal facts the learner shared, for the partner's memory. Empty string if nothing notable."
    )


PROMPT = """# Role
You are the private post-session debrief for a German language-tandem partner named Lena. The learner just had a free German conversation with her. You do three things here, all for internal use — you are NOT talking to the learner:

1. Judge how the session's TARGET grammar patterns went.
2. Harvest any OTHER grammar errors into the fixed taxonomy.
3. Write a short memory note for next time.

# What you get
- topic: what the learner chose to talk about.
- targets: the grammar patterns Lena was quietly steering toward this session, one per line as `id — label: description`. May be empty (a fresh ledger).
- taxonomy: the full catalog of German grammar-error patterns, one per line as `id — label ("wrong" → "right")`, for classifying NON-target errors.
- transcript: the conversation. `User:` lines are the LEARNER; `Bot:` lines are Lena, a native speaker — never classify Lena's German.

# 1. Target patterns
For EACH target, in the same order, decide:
- elicited: did this structure actually come up in the LEARNER's own German this session? Judge the learner's production, not whether Lena used it.
- produced_correctly: did the learner use it correctly and on their own at least once? One clean spontaneous correct use is enough, even if they also slipped once elsewhere. False if it never came up, or every attempt was wrong.
- evidence: the learner's German sentence that shows it — the correct one if produced_correctly, otherwise the clearest slip. "none" if it never came up.
- corrected: ONLY if they got it wrong — the minimal German repair of the evidence sentence. Empty string otherwise.
- note: at most 10 English words on what happened with this structure.

# 2. New errors
Separately, find grammar mistakes the learner made in German that are NOT one of the target patterns, and map each to the ONE taxonomy id whose wrong→right pair matches it.
- Use ids from the catalog only — never invent one.
- Do NOT include any target pattern here — those are fully handled in section 1.
- A pattern counts once: if the learner breaks the same non-target rule several times, emit ONE entry with the clearest example.
- Skip purely lexical slips (a misremembered noun gender, a wrong word choice), turns that are not German, and anything that matches no catalog pattern.

# 3. Session note
Write 2–3 short English lines Lena would want to remember for next time: today's topic and any personal facts the learner shared (job, family, plans, preferences, opinions). Warm and factual, present tense. Empty string if they shared nothing notable.

# Output
Respond with ONLY valid JSON, no surrounding text, no code fences, no comments:
{
  "patterns": [
    {"pattern_id": "<a target id, verbatim>", "elicited": <true|false>, "produced_correctly": <true|false>, "evidence": "<learner sentence or 'none'>", "corrected": "<repair if wrong, else empty>", "note": "<AT MOST 10 English words>"}
  ],
  "new_errors": [
    {"pattern_id": "<a catalog id NOT in targets>", "sentence": "<learner's German sentence>", "corrected": "<minimal German repair>", "note": "<AT MOST 10 English words>"}
  ],
  "session_note": "<2-3 English lines, or empty>"
}
One "patterns" entry per target, same order. Empty "patterns" when there are no targets. Empty "new_errors" when there is nothing else to classify.

# Example
topic: Your weekend
targets:
- perfekt-aux-sein — Perfekt with sein for movement/change ("ich habe gegangen" → "ich bin gegangen")
- nebensatz-verbende — weil/dass/wenn send the verb to the end ("weil ich bin müde" → "weil ich müde bin")
taxonomy (excerpt):
- perfekt-aux-sein — Perfekt with sein for movement/change ("ich habe gegangen" → "ich bin gegangen")
- nebensatz-verbende — weil/dass/wenn send the verb to the end ("weil ich bin müde" → "weil ich müde bin")
- akkusativ-artikel — Accusative article for direct objects ("ich sehe der Mann" → "ich sehe den Mann")

transcript:
Bot: Hallo! Wie war dein Wochenende?
User: Sehr schön! Ich bin ins Kino gegangen, weil ich einen neuen Film sehen wollte.
Bot: Oh schön! Und am Sonntag?
User: Ich habe der Park besucht mit meiner Schwester.

{
  "patterns": [
    {
      "pattern_id": "perfekt-aux-sein",
      "elicited": true,
      "produced_correctly": true,
      "evidence": "Ich bin ins Kino gegangen.",
      "corrected": "",
      "note": "Used sein correctly for movement"
    },
    {
      "pattern_id": "nebensatz-verbende",
      "elicited": true,
      "produced_correctly": true,
      "evidence": "weil ich einen neuen Film sehen wollte",
      "corrected": "",
      "note": "Verb correctly at the end after weil"
    }
  ],
  "new_errors": [
    {
      "pattern_id": "akkusativ-artikel",
      "sentence": "Ich habe der Park besucht mit meiner Schwester.",
      "corrected": "Ich habe den Park besucht mit meiner Schwester.",
      "note": "direct object takes the accusative article"
    }
  ],
  "session_note": "Talked about their weekend. Went to the cinema for a new film; has a sister they spend time with."
}

# Now debrief
topic: {topic}
targets:
{targets}
taxonomy:
{taxonomy}
transcript:
{transcript}
"""


def _render_targets(focus: list[dict]) -> str:
    """One line per target pattern: ``id — label: description``.

    ``focus`` is the enriched list from ``database.repository.load_grammar_focus``
    (the same patterns the session's prompt steered toward). Empty focus — a
    fresh ledger — renders a sentinel line so the model still returns an empty
    ``patterns`` list and just harvests new errors + writes the note.
    """
    if not focus:
        return "(none — this is a fresh ledger; return an empty \"patterns\" list and only harvest new errors + write the note)"
    return "\n".join(
        f"- {p['pattern_id']} — {p['label']}: {p['description']}" for p in focus
    )


async def debrief(
    *, transcript: str, focus: list[dict], topic: str = "", session_id: str | None = None
) -> TandemDebrief:
    """Judge the tandem session: target-pattern outcomes + new errors + memory note.

    ``focus`` is the session's target patterns (from ``load_grammar_focus``).
    Returns a :class:`TandemDebrief` whose ``patterns`` are guaranteed to name
    only real target ids and whose ``new_errors`` name only valid catalog ids
    that are NOT targets (both deduped, first-seen wins) — so the caller applies
    each pattern to the ledger exactly once.

    ``session_id`` (OBS-006) files the debrief's Langfuse trace into the same
    Session as the conversation turns it judges.
    """
    target_ids = {p["pattern_id"] for p in focus}
    rendered = (
        PROMPT
        .replace("{topic}", topic or "(they chose freely)")
        .replace("{targets}", _render_targets(focus))
        .replace("{taxonomy}", taxonomy_brief())
        .replace("{transcript}", transcript)
    )
    llm = ChatOpenAI(
        model=DEBRIEF_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        timeout=30,
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": True}},
    ).with_structured_output(TandemDebrief, include_raw=True)
    with generation_span(
        "tandem-debrief",
        model=DEBRIEF_MODEL,
        input_text=transcript,
        session_id=session_id,
    ) as span:
        result, usage = unwrap_structured_output(await llm.ainvoke(rendered))
        record_generation_output(span, result.model_dump_json(), usage)

    # Keep only real target ids in `patterns` (drop any the model invented or
    # duplicated), preserving order — these drive the streak/retire lifecycle.
    seen_targets: set[str] = set()
    kept_patterns: list[PatternOutcome] = []
    for p in result.patterns:
        if p.pattern_id not in target_ids or p.pattern_id in seen_targets:
            continue
        seen_targets.add(p.pattern_id)
        kept_patterns.append(p)
    result.patterns = kept_patterns

    # `new_errors` must be valid catalog ids, NOT targets (those are handled
    # above), and one per distinct pattern — same guard as the drill harvester,
    # so one session bumps a pattern's occurrences by at most one.
    catalog = load_taxonomy()
    seen_new: set[str] = set()
    kept_new: list[NewError] = []
    for e in result.new_errors:
        if (
            e.pattern_id not in catalog
            or e.pattern_id in target_ids
            or e.pattern_id in seen_new
        ):
            continue
        seen_new.add(e.pattern_id)
        kept_new.append(e)
    result.new_errors = kept_new
    return result
