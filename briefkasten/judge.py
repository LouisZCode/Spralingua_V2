"""The two graded passes over a Briefkasten reply.

The exercise's whole pedagogy is that these are DIFFERENT passes, not one
judge called twice:

- :func:`hint_pass` (attempt 1) names *where* something is wrong and *what
  kind* of thing it is, and never gives the correct form. The learner then
  revises the same letter. This is the output-hypothesis argument the repo
  already runs on (SATZ-003/SATZ-015): being told the answer teaches less
  than being pointed at the problem and producing the fix yourself.
- :func:`feedback_pass` (attempt 2) is the payoff — corrections, reasons,
  a score, and what got better since the first try.

``natural_version`` on the second pass is IDIOM-002 landing here: not "your
letter with the errors fixed" (that is ``corrected_text``) but "how a German
would actually have written this", in this letter's register. It is nullable
ON PURPOSE and the null case is the common one — see the prompt.

Same ``structured_judge_llm`` wiring as every other judge in the repo.
"""

from typing import Literal, Optional

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from briefkasten.content import word_target

JUDGE_MODEL = "openai/gpt-oss-120b"


# SIZE BUDGET: Cerebras strict json_schema caps the generated schema at 5,000
# characters. Class docstrings AND every Field description below count toward
# it. Re-measure with `uv run python speedtest/strict_lint.py` before adding
# fields or lengthening descriptions — overflow isn't fatal (Cerebras rejects,
# the OpenRouter fallback answers, a WARNING is logged) but every call gets
# slower, so trim prose instead.
class HintItem(BaseModel):
    """One thing to look at again — never the fix itself."""

    category: Literal["grammatik", "wortschatz", "rechtschreibung", "wortstellung"] = Field(
        description="Which kind of problem this is"
    )
    text: str = Field(
        description="The learner's own phrase, quoted exactly as they wrote it"
    )
    hint: str = Field(
        description=(
            "ONE short English line pointing at the rule to check. NEVER "
            "contains the corrected form, the right article, or the right "
            "word order"
        )
    )


class HintVerdict(BaseModel):
    """Attempt 1: where to look, not what to write."""

    items: list[HintItem] = Field(
        description=(
            "At most 6, most important first. Empty when the letter has no "
            "real errors"
        )
    )
    message: str = Field(
        description=(
            "One warm English line telling the learner what to focus on in "
            "their revision"
        )
    )
    covered_points: list[bool] = Field(
        description=(
            "Exactly 4 entries, in order, one per required point: did the "
            "letter actually address it?"
        )
    )


class Correction(BaseModel):
    """One correction, with its reason."""

    error: str = Field(description="What the learner wrote, quoted exactly")
    correction: str = Field(description="The corrected German")
    why: str = Field(description="One short English line naming the rule")


class FeedbackVerdict(BaseModel):
    """Attempt 2: the full read on the finished letter."""

    corrected_text: str = Field(
        description=(
            "The learner's letter with its errors repaired. Keep THEIR ideas, "
            "THEIR content and THEIR voice — change only what is wrong"
        )
    )
    explanations: list[Correction] = Field(
        description="At most 5, the ones most worth understanding"
    )
    focus_points: list[str] = Field(
        description="2-3 short English lines: what to practise next"
    )
    score: int = Field(
        description="0-100, judged against what is expected at this letter's level"
    )
    feedback: str = Field(
        description="Warm English summary, 1-2 sentences, honest but never harsh"
    )
    improvements_from_first: str = Field(
        description="One English line on what genuinely got better since attempt 1"
    )
    natural_version: Optional[str] = Field(
        default=None,
        description=(
            "The letter rewritten the way a German would really have written "
            "it in this register. MUST be null unless a native would "
            "genuinely phrase things differently"
        ),
    )


# The German error categories carried over from Spralingua v1's email
# exercise, where they were tuned against real learner letters. Shared by both
# passes so the hint the learner gets and the correction they get later are
# talking about the same things.
_ERROR_CATEGORIES = """- Cases: nominative/accusative/dative/genitive after verbs and prepositions
- Verb position: verb second in main clauses, verb LAST after weil/dass/wenn/obwohl/da, verb first in yes-no questions
- Adjective endings after der/die/das, ein/eine, and with no article
- Noun capitalization: every noun, and only nouns (not adjectives, not verbs, not pronouns other than sentence-initial)
- Umlauts and ß where they belong
- Register consistency: du-forms all the way through an informal letter, Sie-forms all the way through a formal one"""


HINT_PROMPT = """# Role
You are reading a German learner's first draft of a letter. Your job is to point at what needs another look — and NOT to fix it. They are about to revise this same letter, and the whole value of the exercise is that THEY find the fix.

# The letter they received
{letter}

# What they had to address
{points}

# What they wrote
{response}

# Level and register
They are writing at level {level}, in {register} register ({address_form}), aiming for {min_words}-{max_words} words.

# The rule that defines this task
A hint says WHERE the problem is and WHICH rule to check. It never contains the answer.

GOOD hints:
- "Check the case after 'mit' — which one does it always take?"
- "'weil' changes where the verb goes. Where should it be?"
- "This is a noun. What do German nouns always start with?"
- "You switched to 'du' here, but this letter is formal."

FORBIDDEN — every one of these gives the answer away:
- "It should be 'mit dem Zug'."
- "Use 'dem' instead of 'den'."
- "The verb goes to the end: '...weil ich müde bin.'"
If any hint you write contains the corrected form, you have failed this task. Re-read each hint before you finish and delete the answer from it.

## The capitalization trap — this is where hints leak most often
When the error is a missing capital letter, naming the word IS giving the answer: writing 'the noun "Wetter"' hands over the exact fix. So NEVER write the word in its corrected spelling. Either quote it the way THEY wrote it, or describe it without spelling it:
- WRONG: 'Make sure the noun "Wetter" is capitalized.'
- RIGHT: 'One word in this question is a noun — what do German nouns start with?'
- RIGHT: 'Three words here should start differently. Which ones are nouns?'
The same rule holds for every category: if repeating the word requires you to spell it correctly, describe its position instead of naming it.

# What counts as an error
{categories}

Style and word choice are NOT errors. Only flag vocabulary when the word is genuinely wrong or does not exist — never because you would have picked a different one. Do not flag a sentence for being simple.

# `items`
At most 6, the ones that matter most. If the letter is genuinely clean, return an empty list — do not invent problems to look useful.

# `covered_points`
Exactly 4 booleans, in the same order as the required points above. True when the letter really addressed that point, false when it skipped it or only gestured at it.

# `message`
One warm English line aimed at their revision. Name the single most valuable thing to fix. If the letter is clean, say so and tell them to send it.

# Output discipline
`text` quotes their German exactly. `hint` and `message` are English.
"""


FEEDBACK_PROMPT = """# Role
You are giving a German learner the full read on the letter they just revised. They have already had one round of hints and rewritten it. This is the payoff — now you correct, explain, and score.

# The letter they received
{letter}

# What they had to address
{points}

# Their first draft
{first_attempt}

# Their revised letter — this is what you are judging
{second_attempt}

# Level and register
Level {level}, {register} register ({address_form}), target {min_words}-{max_words} words.

# `corrected_text`
Their revised letter with the errors repaired. Keep their ideas, their content, their voice — repair what is wrong and change nothing else. Do not make it more sophisticated, do not add content they did not write, do not shorten it.

# What counts as an error
{categories}

Style is not an error. Do not "correct" a sentence for being simple or for a word choice you merely prefer.

# `explanations`
At most 5 — the ones most worth understanding, not every comma. Each names the rule in one short English line. Skip anything already obvious from the correction.

# `score`
0-100, against what is expected AT LEVEL {level} — not against a native. A letter that addresses all four points in correct, simple, level-appropriate German scores high even if it is plain. Weigh: did it address the four points, is the register consistent, is the grammar sound for this level, is it roughly the right length. Do not deduct for simplicity.

# `improvements_from_first`
One honest English line comparing the two drafts. If nothing improved, say that kindly. Never invent progress.

# `natural_version` — read this twice
This is NOT "their letter with errors fixed" (that is `corrected_text`). It is how a German would ACTUALLY have written this letter — the phrasings a native reaches for that a learner never would.

Write one when the letter contains German that is CORRECT but that no native would produce — most often English translated word for word. These are exactly the cases to catch:
- "Ich bin glücklich zu hören, dass..." → a German writes "Schön, dass..." or "Das freut mich."
- "Lass mich wissen, wann..." → "Sag mir Bescheid, wann..."
- "Ich frage mich, wie das Wetter ist" → "Wie ist denn das Wetter bei dir?"
- "Ich hatte eine sehr beschäftigte Woche" → "Ich hatte viel zu tun."
- "Ich werde dich besuchen kommen" → "Dann komme ich vorbei."
A letter with two or three of these gets a `natural_version`. That is a real, teachable gap and the learner wants to see it.

Return null when the letter already reads like German — simple, plain German counts as natural. Do NOT rewrite to add sophistication, vary word choice, or make it more interesting. Plain but native is the target, not impressive.

The test is: would a German notice something is off? If yes, rewrite. If they would simply read it and reply, return null.

When you do write one, match the register: {register_guidance}

# Output discipline
`corrected_text`, `natural_version`, and the `correction` field of each explanation are German. `feedback`, `focus_points`, `why` and `improvements_from_first` are English.
"""

_REGISTER_GUIDANCE = {
    "informal": (
        "everyday spoken-flavoured German between friends — the way people "
        "really write to each other, not textbook German"
    ),
    "formal": (
        "real German business/official register — the set phrases a native "
        "actually uses in a letter to a landlord, an office or an insurer"
    ),
}

_ADDRESS_FORM = {"informal": "du", "formal": "Sie"}


def _common(seed_level: str, register: str) -> dict[str, str]:
    min_words, max_words = word_target(seed_level)
    return {
        "{level}": seed_level.upper(),
        "{register}": register,
        "{address_form}": _ADDRESS_FORM.get(register, "du"),
        "{min_words}": str(min_words),
        "{max_words}": str(max_words),
        "{categories}": _ERROR_CATEGORIES,
    }


def _render(template: str, mapping: dict[str, str]) -> str:
    """``str.replace`` rather than ``str.format`` — the prompts carry literal
    braces nowhere, but the learner's own text might, and one stray brace in a
    letter would blow up ``format``."""
    out = template
    for key, value in mapping.items():
        out = out.replace(key, value)
    return out


async def hint_pass(
    *,
    letter: str,
    points: list[str],
    response: str,
    register: str,
    level: str,
    user_id: str | None = None,
) -> HintVerdict:
    """Attempt 1: point at the problems, never hand over the fixes."""
    mapping = _common(level, register) | {
        "{letter}": letter,
        "{points}": "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points)),
        "{response}": response,
    }
    prompt = _render(HINT_PROMPT, mapping)
    llm = structured_judge_llm(HintVerdict)
    with generation_span(
        "briefkasten-hints", model=JUDGE_MODEL, input_text=response, user_id=user_id
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result


async def feedback_pass(
    *,
    letter: str,
    points: list[str],
    first_attempt: str,
    second_attempt: str,
    register: str,
    level: str,
    user_id: str | None = None,
) -> FeedbackVerdict:
    """Attempt 2: corrections, reasons, score, and the natural-German read."""
    mapping = _common(level, register) | {
        "{letter}": letter,
        "{points}": "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points)),
        "{first_attempt}": first_attempt,
        "{second_attempt}": second_attempt,
        "{register_guidance}": _REGISTER_GUIDANCE.get(
            register, _REGISTER_GUIDANCE["informal"]
        ),
    }
    prompt = _render(FEEDBACK_PROMPT, mapping)
    # 20s like the writer: this one generates a whole corrected letter plus
    # explanations, so the 12s default would fall back to OpenRouter routinely.
    llm = structured_judge_llm(FeedbackVerdict, deadline_s=20.0)
    with generation_span(
        "briefkasten-feedback",
        model=JUDGE_MODEL,
        input_text=second_attempt,
        user_id=user_id,
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    # Defensive: a judge that returns an empty string for "no rewrite needed"
    # instead of null would render an empty card in the UI.
    if result.natural_version is not None and not result.natural_version.strip():
        result.natural_version = None
    return result
