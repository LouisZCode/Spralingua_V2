"""Germanize pass over a finished Briefkasten letter (IDIOM-002 for letters).

Runs strictly AFTER ``briefkasten/judge.py::feedback_pass`` has already fixed
every grammar mistake in the learner's letter — this call only asks whether
the RESULT is something a native would have WRITTEN, not merely something
that is correct. Modeled closely on ``idiom/judge.py`` (IDIOM-002's proven
rephrase judge): the same tiny schema shape, the same isolate-the-target
prompt structure, and the same deterministic guards run AFTER the model call
rather than trusted to the model alone. Two things idiom's judge carries that
this one drops: the speech-recognition step (a letter is typed, never
transcribed, so there is no ASR artifact to excuse) and the target-word-
evidence guard (a letter has no single practised word to protect).

``natural_version`` used to live on ``FeedbackVerdict`` itself
(``briefkasten/judge.py``) — one combined call asking a single model to both
correct AND naturalize a whole letter in one pass. Splitting it into its own
call buys the same thing IDIOM-002 already proved for spoken turns: a judge
with ONE question asked well beats a judge with two questions asked
passably, and the null case (plain-but-native, no rewrite) is common enough
to deserve its own guard rails rather than riding along inside a bigger
schema's optional field.
"""

from typing import Optional

from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm

JUDGE_MODEL = "openai/gpt-oss-120b"


# SIZE BUDGET: Cerebras strict json_schema caps the generated schema at 5,000
# characters — class docstring and every Field description count. This
# schema is two fields, nowhere near the cap; re-measure before adding a
# third (see the inline check this module was verified against).
class LetterGermanize(BaseModel):
    """One naturalness read of an already-corrected German letter. Never a
    grammar verdict — the grammar pass already ran, this is phrasing only."""

    natural: Optional[str] = Field(
        default=None,
        description=(
            "The letter rewritten the way a German would really have "
            "written it in this register. MUST be null unless a native "
            "would genuinely phrase things differently — plain, simple "
            "German that a native would also write gets null, not a polish"
        ),
    )
    # Mirrors idiom/judge.py::IdiomRephrase.changes_meaning (IDIOM-004 P2): a
    # rewrite that says something different is a wrong letter with correct
    # grammar, not a phrasing fix. This is the model's own self-check;
    # germanize_letter also runs a deterministic negation scan afterward
    # that does not depend on the model getting this flag right.
    changes_meaning: bool = Field(
        description=(
            "True if the rewrite in `natural` adds, removes or negates any "
            "proposition, changes a tense, a person, a date, or an amount "
            "relative to the letter — even a small one. False when "
            "`natural` is null, or when it says exactly the same thing, "
            "only phrased the way a German actually would"
        )
    )


_REGISTER_GUIDANCE = {
    "informal": (
        "everyday written German between people who know each other — the "
        "way a real friend writes a letter, not textbook German"
    ),
    "formal": (
        "real German business/official register — the set phrases a native "
        "actually uses writing to a landlord, an office or an insurer"
    ),
}


PROMPT = """# Role
You are a native German speaker reading ONE letter a German learner just wrote — every grammar mistake in it has already been found and fixed by an earlier pass. Your only question: would a German have WRITTEN it this way? If yes, you stay silent. If not, you show how a German would really write it.

# The letter — grammar already corrected, judge phrasing only
{letter}

# What it replies to (context only)
{context}

# STEP 1 — judge the letter alone
The text above under "What it replies to" exists only so you understand the situation. It is someone else's letter — it is NOT yours to rewrite or comment on. Everything you return is about the learner's letter only.

# STEP 2 — naturalness, not grammar
Nothing in this letter is grammatically wrong — that was a different pass's job, already done before you ever saw it. You are not a grammar checker: never name a rule, never point at a case, an ending, or a missing capital. Your only question is whether the PHRASING is something a native reaches for, or English wearing German words.

# `natural` — read this twice
Write one when the letter contains German that is CORRECT but that no native would produce — most often English translated word for word. These are exactly the cases to catch:
- "Ich bin glücklich zu hören, dass..." → a German writes "Schön, dass..." or "Das freut mich."
- "Lass mich wissen, wann..." → "Sag mir Bescheid, wann..."
- "Ich hatte eine sehr beschäftigte Woche" → "Ich hatte viel zu tun."
- "Ich werde dich besuchen kommen" → "Dann komme ich vorbei."

CONTROL — a plain, already-native letter needs no rewrite: "Hallo Anna, vielen Dank für deinen Brief. Mir geht es gut, danke der Nachfrage. Am Wochenende war ich bei meinen Eltern. Wie geht es dir? Viele Grüße, Lukas" → natural: null. Every line here is exactly how a German writes to a friend — plain and simple IS the target, not a reason to add sophistication.

Return null when the letter already reads like German. Do NOT rewrite to add sophistication, vary word choice, or make it more interesting — a rewrite that merely sounds fancier than the original is not what this is for.

# STEP 3 — never change what was said
Adding, dropping or flipping a negation, a date, a name, or an amount — anything that makes the rewrite state a different fact than the letter did — is not a rephrase, it is a different letter wearing the same words.
- "Der Termin am Montag passt mir leider nicht." (does NOT suit them) is not "Der Termin am Montag passt mir." (DOES suit them). If your first instinct produces something like this, that is `changes_meaning: true` and `natural` goes back to null — do not return the changed version.

CONTROL — "Ich habe am Dienstag keine Zeit, aber am Donnerstag würde es bei mir gut passen." has a real phrasing fix worth making ("bei mir gut passen" → "mir gut passen") without ever touching WHICH day has time and which doesn't — rewrite the wording, never the facts underneath it.

The test is: would a German notice something is off in how it's WRITTEN? If yes, rewrite. If they would simply read it and reply, return null.

Keep the same shape as the letter — greeting, paragraphs, closing, nothing added or removed, nothing summarized away. Register: {register_guidance}
"""


def _normalized(s: str) -> str:
    """Casefold and strip everything but letters/digits — copied from
    idiom/judge.py: two letters that differ only in spacing, casing, or
    punctuation are the same letter."""
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def _token_multiset(s: str) -> list[str]:
    """The letter's words, casefolded and order-blind — copied from
    idiom/judge.py. A 'rewrite' with the identical multiset only moved
    words around, which is grammar/word-order territory, not phrasing."""
    return sorted(
        "".join(ch if ch.isalnum() else " " for ch in s.casefold()).split()
    )


# Copied from idiom/judge.py (IDIOM-004 P2): negation markers, order-blind. A
# rewrite that adds or drops one of these relative to the input has flipped
# what the letter asserted — this check does not trust the model's own
# `changes_meaning` flag. `kein` is matched by prefix so keine/keinen/
# keinem/keiner/keines all count without listing every inflection.
_NEGATION_WORDS = {"nicht", "nie", "niemand", "niemanden", "niemandem", "nichts"}


def _negation_tokens(s: str) -> list[str]:
    tokens = "".join(ch if ch.isalnum() else " " for ch in s.casefold()).split()
    return sorted(t for t in tokens if t in _NEGATION_WORDS or t.startswith("kein"))


async def germanize_letter(
    corrected_text: str,
    task_context: str,
    register: str = "informal",
    user_id: str | None = None,
) -> str | None:
    """One structured-output naturalness rewrite of an already-corrected
    letter, or ``None`` when it already reads like German.

    ``corrected_text`` is ``FeedbackVerdict.corrected_text`` — grammar is
    fixed upstream, this call only asks about phrasing. ``task_context`` is
    the incoming letter the learner was replying to (context only, never
    graded — the isolate-the-target step in the prompt exists for exactly
    this reason). Any judge-call failure propagates to the caller
    (``briefkasten/routes.py``), which treats a Germanize outage as
    ``None`` rather than failing the attempt.
    """
    # This GENERATES a rewrite rather than returning a verdict, same
    # temperature=None opt-out as briefkasten/writer.py's write_letter (see
    # the CLAUDE.md call-site list this module was added to): the same
    # letter should be able to naturalize differently across attempts, not
    # produce the identical rewrite every time.
    llm = structured_judge_llm(LetterGermanize, temperature=None)
    prompt = (
        PROMPT.replace("{letter}", corrected_text)
        .replace("{context}", task_context or "(none given)")
        .replace(
            "{register_guidance}",
            _REGISTER_GUIDANCE.get(register, _REGISTER_GUIDANCE["informal"]),
        )
    )
    with generation_span(
        "briefkasten-germanize", model=JUDGE_MODEL, input_text=prompt, user_id=user_id
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)

    if result.natural is None:
        return None

    candidate = result.natural.strip()
    # Same deterministic guards as idiom/judge.py::germanize, minus the
    # target-word-evidence guard (a letter has no single practised word).
    discard = (
        not candidate
        or _normalized(candidate) == _normalized(corrected_text)
        or _token_multiset(candidate) == _token_multiset(corrected_text)
        or result.changes_meaning
        or _negation_tokens(candidate) != _negation_tokens(corrected_text)
    )
    return None if discard else candidate
