"""Written-attempt diagnosis for Feste Verbindungen (GRAM-002, Exercise D).

Only MISSES reach this module (the route's deterministic check green-lights
exact/embedded matches at zero LLM cost). Where Bauteil's judge separates
case from carrier, the chunk drill's axes are the chunk's ELEMENTS — the
judge names exactly which one broke: the reflexive pronoun (missing, extra,
or wrong), the fixed preposition, the case it governs, or the da-/wo-
compound. Same Cerebras ``gpt-oss-120b`` wiring as the sibling judges.
"""

from typing import Optional

from agents.openrouter_llm import ProviderChatOpenAI
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from config import openrouter_api_key, openrouter_base_url

JUDGE_MODEL = "openai/gpt-oss-120b"


class Diagnosis(BaseModel):
    """Verdict on a mismatched chunk completion."""

    correct: bool = Field(
        description=(
            "True ONLY when the learner's words fill the gap fully correctly "
            "and differ from the expected solution trivially (frame words "
            "typed around it, punctuation, capitalization). A missing/extra/"
            "wrong pronoun, preposition, article, case or compound is NEVER "
            "trivial"
        )
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "One short English line (AT MOST 14 words) naming exactly which "
            "element broke and why; null when correct"
        ),
    )


PROMPT = """# Role
You diagnose one WRITTEN attempt in a German verb-chunk drill ("Feste Verbindungen"). The learner saw a sentence with a gap and had to type the missing words of a fixed verb combination — the reflexive pronoun (only if the verb takes one), the fixed preposition, and/or the article in the case the preposition governs.

# The item
- sentence: "{frame}"
- the chunk being tested: {chunk}
- correct solution for the gap: "{expected}"

# What the learner typed
"{typed}"

# How to diagnose
- `correct` — true ONLY when the learner's words fill the gap fully correctly and differ from the expected solution trivially: frame words typed around it, punctuation, capitalization. A missing/extra/wrong reflexive pronoun, a different preposition, a wrong article or case, or a malformed da-/wo-compound is NEVER trivial.
- `note` — when correct=false: ONE short English line (AT MOST 14 words) naming exactly WHICH element broke and teaching the why: the reflexive pronoun (missing where the verb demands one, added where it doesn't, or the wrong person), the fixed preposition (wrong one), the case the preposition governs (wrong article), or the da-/wo-compound (malformed or split). The correct chunk is shown to the learner separately — never restate the full fix. Good notes: "'warten' isn't reflexive — no pronoun needed." / "'sich freuen auf' — the fixed preposition is 'auf', not 'über'." / "'vor' after 'Angst haben' takes the dative." / "Preposition + 'es' fuses into one word: da + auf." When correct=true: null.
"""


async def judge_chunk(item: dict, typed: str) -> Diagnosis:
    """One structured-output diagnosis call over the item + typed answer."""
    llm = ProviderChatOpenAI(
        model=JUDGE_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        timeout=30,
        # OBS-008: pinned, no fallback off Cerebras — see LEARNINGS.md / the
        # asymmetry comment in agents/conversation_agent.py.
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": False}},
    ).with_structured_output(Diagnosis, include_raw=True)
    prompt = (
        PROMPT.replace("{frame}", item["frame"])
        .replace("{chunk}", item["chunk"])
        .replace("{expected}", item["answer"])
        .replace("{typed}", typed)
    )
    # OBS-007: the `verbindungen-judge` child of the route's verbindungen-attempt trace.
    with generation_span("verbindungen-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    if result.correct:
        result.note = None
    return result
