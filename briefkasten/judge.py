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

import re
from dataclasses import dataclass
from typing import Literal, Optional

from loguru import logger
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

    error: str = Field(
        description="What the learner wrote, quoted exactly from the revised letter — never the first draft"
    )
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


# The German error categories carried over from Spralingua v1's email
# exercise, where they were tuned against real learner letters. Shared by both
# passes so the hint the learner gets and the correction they get later are
# talking about the same things.
_ERROR_CATEGORIES = """- Cases: nominative/accusative/dative/genitive after verbs and prepositions
- Verb position: verb second in main clauses, verb LAST after weil/dass/wenn/obwohl/da, verb first in yes-no questions
- Adjective endings after der/die/das, ein/eine, and with no article
- Noun capitalization: every noun, and only nouns (not adjectives, not verbs, not pronouns other than sentence-initial)
- Umlauts and ß where they belong
- Register consistency: du-forms all the way through an informal letter, Sie-forms all the way through a formal one

## The salutation exception — do not over-apply "capitalize the first word"
After a salutation that ends in a comma ("Hallo Anna," / "Liebe Frau Meier," / "Sehr geehrter Herr Klein,"), German continues the letter body in LOWERCASE. The word touching that comma is capitalized ONLY if it would be anyway — a noun, a proper name, or the formal "Sie"/"Ihr". This is standard Duden convention, not a mistake:
- "Hallo Anna,\nein Urlaub klingt sehr spannend!" — "ein" stays lowercase. This is correct as written. Do NOT flag it.
- CONTROL — "Liebe Anna,\nvielen dank für deinen Brief." — "dank" is a noun ("der Dank"), so it is capitalized regardless of position. DO flag it: "dank" → "Dank".
- CONTROL — the exception covers only the single word touching the salutation comma. Every later sentence start is graded normally: "...spannend! wo gehst du hin?" — "wo" opens a NEW sentence after "!", well past the salutation, and must still become "Wo". Flag it."""


# BRIEF-004 P2: register is a hard check, not something left for the LLM to
# weigh alongside everything else. A formal seed's opening line and address
# pronoun are a binary fact the code can check before the LLM ever sees the
# letter — "Hallo Thomas" to a landlord is wrong regardless of how good the
# rest of the letter is. Every pattern below is deliberately NARROW: a false
# register accusation (flagging "sie" that means "she") teaches the learner
# to distrust the grader, which is worse than one miss. When in doubt, the
# rule below does not fire — see the BRIEF-004 replay pack for the specific
# controls each pattern was built and checked against.
_INFORMAL_GREETING_RE = re.compile(r"^\s*((?:Hallo|Hi)\b[^\n,]*)", re.IGNORECASE)
# "Liebe/r <first name>," is informal — but "Liebe Frau Meier," is a seed
# with a surname attached and is NOT what this flags (see BRIEF-004 brief).
_LIEBE_FIRSTNAME_RE = re.compile(
    r"^\s*(Lieber?\b(?!\s+(?:Herr|Frau)\b)[^\n,]*)", re.IGNORECASE
)
# Lowercase "sie"/"ihnen"/"ihr" immediately after a request-shaped modal verb
# ("können sie", "würden sie") is almost always the address pronoun written
# without its required capital — not "she"/"they". The verb is matched
# case-insensitively (a capitalized "Können sie" still counts); the pronoun
# group is NOT — this only fires on the literal lowercase spelling, which is
# the actual mistake. Deliberately NOT "any sie after a comma" — a control in
# the BRIEF-004 brief ("...sagt, sie kommt auch") is exactly why: a comma
# alone says nothing about who "sie" refers to.
_LOWERCASE_ADDRESS_RE = re.compile(
    r"(?i:können|habt|haben|würden|hätten|möchten|sollten|dürften|wären|könnten|geben)"
    r"\s+(sie|ihnen|ihr)\b"
)
_INFORMAL_CLOSING_RE = re.compile(
    r"\b(Viele Grüße|Liebe Grüße|Bis bald|Tschüss)\b", re.IGNORECASE
)
_FORMAL_GREETING_RE = re.compile(r"(Sehr geehrter?\b[^\n,]*)", re.IGNORECASE)
_FORMAL_CLOSING_RE = re.compile(r"\bMit freundlichen Grüßen\b", re.IGNORECASE)
# Capitalized "Sie"/"Ihnen"/"Ihre..." NOT at the very start of a sentence is
# unambiguous in correct German — ordinary words don't capitalize mid-clause,
# so seeing one there means it is the formal address form, not "she"/"they"
# (which only capitalizes when it happens to open a sentence — the genuinely
# ambiguous case, which this deliberately skips).
_SIE_WORD_RE = re.compile(r"\b(Sie|Ihnen|Ihre[nrs]?|Ihres)\b")


def _find_midsentence_sie(text: str) -> str | None:
    for m in _SIE_WORD_RE.finditer(text):
        j = m.start() - 1
        while j >= 0 and text[j] in " \t\n":
            j -= 1
        if j < 0:
            continue  # start of the letter — sentence-initial, skip
        prev = text[j]
        if prev in ".!?":
            continue  # sentence-initial — ambiguous, skip (conservative)
        if prev == "," and "\n" in text[j : m.start()]:
            # The comma closes the greeting line ("Hallo Jonas,\n") — the
            # word after it opens the letter's first sentence, so its capital
            # says nothing about register (T5 tester: "Hallo Jonas,\nSie kommt
            # auch" was flagged and score-capped on a correct informal letter).
            continue
        if prev == "," or (prev.isalpha() and prev.islower()):
            return m.group(0)
    return None


@dataclass
class _RegisterFinding:
    quote: str  # the learner's own phrase that tripped the scan, verbatim
    fixed_greeting: str  # the register-correct greeting for this sender
    pronoun: str  # "him" / "her" / "them" — used in the formal-seed notice


def _formal_greeting(sender_name: str) -> str:
    if sender_name.startswith("Herr"):
        return f"Sehr geehrter {sender_name},"
    if sender_name.startswith("Frau"):
        return f"Sehr geehrte {sender_name},"
    return f"Sehr geehrte(r) {sender_name},"  # no formal seed lacks Herr/Frau today


def _formal_pronoun(sender_name: str) -> str:
    if sender_name.startswith("Herr"):
        return "him"
    if sender_name.startswith("Frau"):
        return "her"
    return "them"


def _register_scan(text: str, register: str, sender_name: str) -> Optional[_RegisterFinding]:
    """Deterministic pre-LLM register check (BRIEF-004 P2). ``None`` when
    nothing fires — the common case, and the only signal `hint_pass` /
    `feedback_pass` act on."""
    stripped = text.strip()
    if register == "formal":
        quote = None
        if m := _INFORMAL_GREETING_RE.match(stripped):
            quote = m.group(1).strip()
        elif m := _LIEBE_FIRSTNAME_RE.match(stripped):
            quote = m.group(1).strip()
        if quote is None and (m := _LOWERCASE_ADDRESS_RE.search(stripped)):
            quote = m.group(0)
        if quote is None and (m := _INFORMAL_CLOSING_RE.search(stripped)):
            quote = m.group(0)
        if quote is None:
            return None
        return _RegisterFinding(
            quote=quote,
            fixed_greeting=_formal_greeting(sender_name),
            pronoun=_formal_pronoun(sender_name),
        )
    if register == "informal":
        quote = None
        if m := _FORMAL_GREETING_RE.search(stripped):
            quote = m.group(1).strip()
        if quote is None and _FORMAL_CLOSING_RE.search(stripped):
            quote = "Mit freundlichen Grüßen"
        if quote is None:
            quote = _find_midsentence_sie(stripped)
        if quote is None:
            return None
        return _RegisterFinding(
            quote=quote, fixed_greeting=f"Hallo {sender_name},", pronoun=""
        )
    return None


def _register_notice(finding: _RegisterFinding, register: str, sender_name: str) -> str:
    """English line telling both the learner (via the fixed item) and the LLM
    (via the prompt) what the scan already established, so neither has to
    rediscover it and the LLM cannot contradict it."""
    if register == "formal":
        return (
            f"This is a formal letter to {sender_name} — open with "
            f"'{finding.fixed_greeting}' and address {finding.pronoun} as "
            f"'Sie' throughout, then close with 'Mit freundlichen Grüßen'."
        )
    return (
        f"This is an informal letter to {sender_name} — open with "
        f"'{finding.fixed_greeting}' and use 'du' throughout, not the formal "
        f"'Sie'."
    )


def _fixed_hint_item(finding: _RegisterFinding, register: str, sender_name: str) -> "HintItem":
    return HintItem(
        category="grammatik",
        text=finding.quote,
        hint=_register_notice(finding, register, sender_name),
    )


def _fixed_correction(finding: _RegisterFinding, register: str, sender_name: str) -> "Correction":
    return Correction(
        error=finding.quote,
        correction=finding.fixed_greeting,
        why="Register consistency: " + _register_notice(finding, register, sender_name),
    )


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
{register_notice}

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
{register_notice}

# `corrected_text`
Their revised letter with the errors repaired. Keep their ideas, their content, their voice — repair what is wrong and change nothing else. Do not make it more sophisticated, do not add content they did not write, do not shorten it.

# What counts as an error
{categories}

Style is not an error. Do not "correct" a sentence for being simple or for a word choice you merely prefer.

# `explanations`
At most 5 — the ones most worth understanding, not every comma. Each names the rule in one short English line. Skip anything already obvious from the correction.

## Quote only the revised letter — the first draft is context, not a source
The first draft above exists so you can judge `improvements_from_first`. It is NOT something you grade or quote from. Before writing an `error` string, check: does this exact phrase appear in "Their revised letter" above? If it appears ONLY in the first draft, the learner already fixed it — leave it out of `explanations` entirely. Telling them to fix something they already fixed is worse than saying nothing.
- First draft: "Ich habe eine Frage über das Wetter." Revised: "Ich habe eine Frage zum Wetter." — the "über"/"zum" error is GONE from the revised letter. Do NOT put it in `explanations`.
- CONTROL — First draft: "Ich freue mich auf dich sehen." Revised: "Ich freue mich auf dich sehen." (unchanged) — the same infinitive-clause error is STILL there in the revised letter, so it MUST be flagged, quoting it from the revised letter exactly as it stands.
- CONTROL — First draft: "der Wetter ist schön." Revised: "das Wetter ist schön, aber ich mag der Regen nicht." — "der Wetter" was fixed to "das Wetter" (do not flag it), but "der Regen" is a new, still-uncorrected error that only exists in the revised letter — flag that one.

## Get the grammar fact right, not just the correction (BRIEF-004 P3)
A correct fix next to a WRONG reason teaches wrong grammar with a right answer standing beside it — that is worse than no explanation. These two are specific facts learners are told wrong often enough to need spelling out:
- "bis der/die Party" → "bis zur Party" — the case here is DATIVE, not accusative. "bis" is combining with "zu" (bis zu + Dativ, reaching an event/destination: zu + der → zur, zu + dem → zum). The `why` MUST say "bis zu + Dativ" (or "zu governs dative, zur = zu + der") — NEVER "bis requires accusative" or "bis takes the accusative".
- "bis zum Wochenende" — same rule, masculine/neuter: zu + dem → zum, still dative, never accusative.
- "ich kann also etwas mitbringen" (meaning "I can also bring something") → "ich kann auch etwas mitbringen". German "also" means "so"/"therefore", not English "also" — it is a false friend, not a case or word-order issue. Fix it in `corrected_text` and, if you explain it, the `why` is "'also' is a false friend — it means 'so/therefore' in German; 'also' in the English sense is 'auch'", never a grammar-rule wording that leaves "also" looking merely stylistic.
- CONTROL — "bis nächsten Montag" (a plain time expression, "bis" alone with no "zu") stays genuinely accusative — "bis" by itself governs the accusative. Do NOT apply the "bis zu + Dativ" rule here; this is correct German as written and must not be flagged or rewritten.

# `score`
0-100, against what is expected AT LEVEL {level} — not against a native. A letter that addresses all four points in correct, simple, level-appropriate German scores high even if it is plain. Weigh: did it address the four points, is the register consistent, is the grammar sound for this level, is it roughly the right length. Do not deduct for simplicity.

# `improvements_from_first`
One honest English line comparing the two drafts. If nothing improved, say that kindly. Never invent progress.
{identical_notice}

# Output discipline
`corrected_text` and the `correction` field of each explanation are German. `feedback`, `focus_points`, `why` and `improvements_from_first` are English.
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
    sender_name: str,
    user_id: str | None = None,
) -> HintVerdict:
    """Attempt 1: point at the problems, never hand over the fixes."""
    # BRIEF-004 P2: run before the LLM so the notice can be handed to it.
    finding = _register_scan(response, register, sender_name)
    register_notice = (
        "REGISTER ALREADY CHECKED — a deterministic scanner already found: "
        + _register_notice(finding, register, sender_name)
        + " This is being shown to the learner directly as the first item. "
        "Do not repeat it as your own item and do not describe the letter's "
        "register as fine."
        if finding
        else ""
    )
    mapping = _common(level, register) | {
        "{letter}": letter,
        "{points}": "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points)),
        "{response}": response,
        "{register_notice}": register_notice,
    }
    prompt = _render(HINT_PROMPT, mapping)
    llm = structured_judge_llm(HintVerdict)
    with generation_span(
        "briefkasten-hints", model=JUDGE_MODEL, input_text=response, user_id=user_id
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    if finding:
        fixed = _fixed_hint_item(finding, register, sender_name)
        result.items = ([fixed] + [i for i in result.items if i.text != fixed.text])[:6]
    return result


def _normalize_ws(text: str) -> str:
    """Whitespace-only normalisation for the identical-resubmit check
    (BRIEF-004 P1) — a re-wrapped but otherwise unchanged letter still counts
    as identical."""
    return " ".join(text.split())


async def feedback_pass(
    *,
    letter: str,
    points: list[str],
    first_attempt: str,
    second_attempt: str,
    register: str,
    level: str,
    sender_name: str,
    user_id: str | None = None,
) -> FeedbackVerdict:
    """Attempt 2: corrections, reasons, score, and the natural-German read."""
    # BRIEF-004 P1: decided in code, not narrated by the LLM. The client
    # echoes attempt 1's text back on attempt 2 (see routes.py) — nothing
    # stops a learner resubmitting unchanged, and an LLM asked to compare two
    # identical drafts will still invent a diff rather than say so.
    identical = _normalize_ws(first_attempt) == _normalize_ws(second_attempt)
    # BRIEF-004 P2: run before the LLM so the notice can be handed to it and
    # the fixed correction is available regardless of what the LLM returns.
    finding = _register_scan(second_attempt, register, sender_name)
    register_notice = (
        "REGISTER ALREADY CHECKED — a deterministic scanner already found: "
        + _register_notice(finding, register, sender_name)
        + " This is being shown to the learner directly as the first "
        "explanation and the score is already capped for it. Your "
        "`corrected_text` MUST fix this — do not leave the flagged "
        "greeting, pronoun, or closing in place — and do not contradict "
        "this finding elsewhere in your output."
        if finding
        else ""
    )
    mapping = _common(level, register) | {
        "{letter}": letter,
        "{points}": "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points)),
        "{first_attempt}": first_attempt,
        "{second_attempt}": second_attempt,
        "{register_guidance}": _REGISTER_GUIDANCE.get(
            register, _REGISTER_GUIDANCE["informal"]
        ),
        "{register_notice}": register_notice,
        "{identical_notice}": (
            "NOTE: attempt 1 and attempt 2 are IDENTICAL (whitespace aside) "
            "— nothing changed. Do not invent or imply any improvement here; "
            "say plainly that nothing changed."
            if identical
            else ""
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

    # BRIEF-004 P1: the LLM's own line is discarded outright when the drafts
    # are identical — the prompt note above is defense in depth, this is the
    # guarantee. A fabricated "you improved X" must never reach the learner.
    if identical:
        result.improvements_from_first = (
            "Same text as your first attempt — nothing new to compare."
        )

    # BRIEF-004 P2: the fixed correction always leads, score is capped
    # regardless of what the LLM decided on its own, and we log (never
    # block) if the corrected letter still carries the flagged phrase.
    if finding:
        fixed = _fixed_correction(finding, register, sender_name)
        result.explanations = (
            [fixed] + [e for e in result.explanations if e.error != fixed.error]
        )[:5]
        # Score is expected 0-100 against the letter's level (see the
        # `score` prompt section); 60 keeps a register-broken letter out of
        # "good" territory (>=70 in the UI's own praise copy) without
        # zeroing out otherwise-solid grammar underneath it.
        result.score = min(result.score, 60)
        if finding.quote.lower() in result.corrected_text.lower():
            logger.info(
                "Briefkasten feedback: corrected_text still contains the "
                "flagged register phrase {!r} (sender {!r})",
                finding.quote,
                sender_name,
            )
    return result
