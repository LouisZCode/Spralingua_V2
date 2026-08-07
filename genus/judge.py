"""Free-text gender judge for Artikel-Anker.

Only sentences the deterministic grader can't parse reach this module — the
bare phrase and the whitelisted carrier sentences stay deterministic and
free. The judge exists so learners can write ANYTHING with the target noun
(user decision 2026-07-22) and be judged on exactly ONE axis: is the noun's
grammatical gender used correctly — any case, any determiner, adjectives
optional. Word order, verb forms, spelling of other words: all ignored, the
Sprechen-judge philosophy. Same Cerebras ``gpt-oss-120b`` structured-output
wiring as ``bauteil/judge.py``.
"""

from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm

JUDGE_MODEL = "openai/gpt-oss-120b"

_GENDER_LABEL = {"der": "masculine", "die": "feminine", "das": "neuter"}


class GenderVerdict(BaseModel):
    """Gender-only verdict on a free-text production sentence."""

    gender_shown: bool = Field(
        description=(
            "False iff the noun appears only in plural or so bare that no "
            "word in the sentence reveals its gender — nothing to judge then"
        )
    )
    correct: bool = Field(
        description=(
            "True iff every gender-dependent word referring to the noun "
            "(articles, determiners, adjective endings, pronouns) agrees "
            "with its true gender, in whatever case the sentence uses — "
            "never affected by any OTHER error in the sentence (spelling, "
            "verb form, word order)"
        )
    )
    wrong_word: str = Field(
        description=(
            "The single wrongly-inflected word copied EXACTLY from the "
            "sentence (the first, if several); empty when correct or when "
            "gender_shown is false"
        )
    )
    note: str = Field(
        description=(
            "One short English line (max 14 words) naming the slip and the "
            "fix; 'ok' when correct"
        )
    )
    corrected: str = Field(
        description=(
            "The learner's sentence with ONLY gender-agreement fixes "
            "applied — every other word, error and quirk kept verbatim"
        )
    )


PROMPT = """# Role
You check ONE thing in a German learner's sentence: whether the grammatical gender of the noun "{noun}" is used correctly. The truth: {noun} is {gender} — "{article} {noun}".

# What counts
- Every word whose form depends on {noun}'s gender: articles (der/die/das, ein...), determiners (kein-, mein-, dies-...), adjective endings, pronouns referring back to it.
- Judge agreement in WHATEVER case the learner's own sentence puts the noun (nominative, accusative, dative, genitive) — any case, any determiner, with or without adjectives, all are welcome.
- A weak masculine noun's own -(e)n form counts too (den Kunden, dem Namen).

# What does NOT count
Ignore everything else completely: word order, verb forms and agreement, spelling of other words, missing words, punctuation, capitalization, style, whether the sentence makes sense. A clumsy sentence with correct gender marking is correct=true.

## This is where graders go wrong
- die Wohnung, typed "Ich habe eine schöne Wohnug gesehen." → correct: true. "Wohnug" is a spelling slip in the noun itself, not a gender-marking word — "eine" and "schöne" both agree correctly.
- der Mann, typed "Der Mann esse gerne Äpfel." → correct: true. "esse" is a wrong verb form, not gender — "Der" agrees correctly.
- das Kind, typed "Ich das Kind sehe jeden Tag." → correct: true. Word order is broken, but "das" still agrees correctly.
- das Auto, typed "Ich habe ein neues Auto, aber der Auto ist kaputt." → correct: false, wrong_word: "der". THIS is a real gender error — "der" wrongly marks das Auto as masculine.

# Output rules
- gender_shown=false when {noun} appears only in plural, or so bare that no word reveals its gender — then set correct=true, wrong_word="", and let note tell the learner to use the singular with an article so the gender shows.
- wrong_word: copy the ONE offending word exactly as the learner typed it (the first one if several).
- corrected: the learner's sentence with only the gender fixes applied; keep every other word — including unrelated mistakes — exactly as typed.
- note: one short English line naming the slip and the fix; "ok" when correct.

# The sentence
"{typed}"
"""


async def judge_gender(item: dict, typed: str) -> GenderVerdict:
    """One structured-output gender check over the learner's free sentence."""
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    llm = structured_judge_llm(GenderVerdict)
    prompt = (
        PROMPT.replace("{noun}", item["noun"])
        .replace("{article}", item["article"])
        .replace("{gender}", _GENDER_LABEL[item["article"]])
        .replace("{typed}", typed)
    )
    # OBS-007: the `genus-judge` child of the route's genus-attempt trace.
    with generation_span("genus-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result
