"""Fact-check gate on LLM-forged Satzschmiede cards (SATZ-021).

``satz/content.py::_validate_card`` only checks a card's SHAPE — a past card
has *a* tense_form, a verb carries no note — never whether the German is
TRUE. A full audit of the 238 production cards (5 auditors + 3 independent
Duden/DWDS verifiers) measured 10.1% of LLM-forged cards carrying a
confirmed factual error, against 0% of hand-written ones. Measured failure
classes, most common first: a gloss naming the wrong sense of a false friend
or homograph; an invented or wrong plural; an example demonstrating a
different verb or sense than the headword; a wrong Perfekt auxiliary; a
``reflexive`` flag contradicting its own gloss/example.

This module is the second opinion: one more structured-output call that
reads the WHOLE forged card and fact-checks it, independent of (and after)
the call that wrote it. ``satz/routes.py`` drives the draft -> verify ->
retry loop; this file only judges.

Same Cerebras ``gpt-oss-120b`` wiring as ``satz/enricher.py``.
"""

import asyncio
from typing import Optional

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from satz.enricher import EnrichedCard

VERIFIER_MODEL = "openai/gpt-oss-120b"


class CardVerdict(BaseModel):
    """Fact-check verdict on one forged Satzschmiede card: is the German
    itself TRUE, not just correctly shaped.

    Deliberately THREE booleans rather than one ``ok``, with the overall
    verdict derived in Python below. The first draft asked for a single
    ``ok`` and let the model form a global impression of the card — it then
    passed both a false-friend gloss ("familiär" = "familiar") and an
    example demonstrating the wrong verb ("Er hielt das Buch **fest**" on a
    *halten* card), **even though both cases sit in the prompt verbatim as
    worked examples**. Splitting the judgement forces the model to answer
    the gloss-versus-example question on its own instead of folding it into
    an overall feeling about a card that otherwise looks tidy. Same lesson
    as SATZ-020's Proposal-2: where a gate can be made structural, don't
    leave it to prompt compliance.
    """

    example_meaning: Optional[str] = Field(
        default=None,
        description=(
            "FIRST, before any verdict: a literal English translation of the "
            "card's example sentence, and which German word it is actually "
            "demonstrating. Null only when the card carries no example. "
            "Writing this out is what makes the next answer honest"
        )
    )
    gloss_sense_ok: bool = Field(
        description=(
            "Does the example use the word in the SAME sense the gloss "
            "names? False for a false friend, for a homograph glossed with "
            "the wrong sense, or when the example actually demonstrates a "
            "different (e.g. prefixed) word. True when there is no example"
        )
    )
    forms_ok: bool = Field(
        description=(
            "Are the stated grammar facts real? The noun's gender and "
            "plural, the adjective's comparison forms, the preposition's "
            "case, an adverb's note (only real when it states a genuine "
            "comparison — never an invented one), and for verbs the past "
            "form with the auxiliary that verb actually takes. True when "
            "the card states no such facts"
        )
    )
    example_ok: bool = Field(
        description=(
            "Is every example sentence grammatical, natural German that "
            "genuinely uses THIS headword? True when there is no example"
        )
    )
    problem: Optional[str] = Field(
        default=None,
        description=(
            "REQUIRED when ok=false: ONE short, specific English line "
            "naming exactly what is wrong, e.g. \"gloss says 'to deal "
            "with' but the example shows the inseparable umgehen = to "
            "avoid\". Fed back to the card writer to fix. Null when ok"
        ),
    )

    @property
    def ok(self) -> bool:
        """The card passes only when every check passed — computed here, not
        asked of the model, so a tidy-looking card cannot talk its way past
        a specific failure."""
        return self.gloss_sense_ok and self.forms_ok and self.example_ok


PROMPT = """# Role
You fact-check ONE German vocabulary flashcard before it reaches a learner ("Satzschmiede"). This is a truth check, not a style check — the card's SHAPE (which fields it carries) was already validated elsewhere; you check whether the German ON it is actually correct.

# The card
{facts}

# STEP 1 — isolate what you are checking
Read every field as one connected claim about ONE word: the gloss claims a meaning, the example must SHOW that same meaning, the note (nouns: plural; adjectives: comparison; prepositions: case; adverbs: none, or a real comparison (gern → lieber)) must be the real grammatical fact, and — for a verb — the past form's auxiliary must be the one that verb actually takes. A card is wrong if ANY of these disagree with real German, even when every field looks fine read on its own.

# What breaks a card — check every one
(a) Gloss vs. example, same sense. The gloss names ONE meaning; the example must use the word in THAT meaning, not a different sense and not a different (e.g. separable-prefix) verb that merely looks related. A false friend or a homograph glossed with the wrong sense is the single most common failure.
(b) Perfekt auxiliary. Every past form must carry the auxiliary that verb actually takes. Motion and change-of-state verbs (fliegen, gehen, kommen, aufstehen, sterben, werden, and verbs like beitreten) take SEIN in the Perfekt — "hat" on one of these is wrong even if the rest of the form looks fine.
(c) Invented or wrong grammar facts — a noun's plural that Duden does not list (some nouns genuinely have none, and "no plural" is a correct note; an invented plural form is not), a wrong gender, a wrong preposition case.
(d) Internal inconsistency — e.g. reflexive marked false while the gloss and example both use the word reflexively.

# Worked examples
Note how each one fails ONE specific check while the others pass — that is why they are answered separately.

1. adjective "familiär" · gloss "familiar" · example "Die Atmosphäre ist sehr familiär." → `gloss_sense_ok: false`, forms_ok true, example_ok true. Translate the example first: "the atmosphere is very cosy/informal". That is not "familiar" (= vertraut/bekannt) — "familiär" is a false friend, so the gloss names a sense the example does not show. The sentence itself is perfectly good German, which is exactly why only the first check fails.
2. noun "Wachstum" · note "neuter · plural: die Wachstums" → `forms_ok: false`, the others true. "Wachstum" has no plural; the note invents one. `problem` must say "no plural" — do not swap in a different invented form.
3. verb "halten" · gloss "to hold" · past_example "Er hielt das Buch fest." → `gloss_sense_ok: false`, the others true. The sentence is correct German, but "festhalten" (to hold onto) is a different, separable verb from the headword "halten" — the example demonstrates the wrong word, not a sense of this one.
4. verb "beitreten" · past_form "hat beigetreten" → `forms_ok: false`, the others true. "beitreten" (to join) is a change-of-state verb; its Perfekt takes sein: "ist beigetreten".
5. verb "vorstellen" · gloss "to introduce oneself" · past_example "Ich habe mir das Ergebnis vorgestellt." → `gloss_sense_ok: false`, the others true. "sich³ etwas vorstellen" means "to imagine something" — a different sense of the same verb from the one the gloss names.
6. noun "Termin" · article "der" · note "masculine · plural: die Termine" · gloss "appointment" · example "Ich habe morgen einen wichtigen Termin." → all three TRUE. CONTROL — gender, plural, gloss and example all agree and are correct German. A genuinely fine card must pass; do not reject on principle.

# Verdict — answer these THREE questions separately
Do not form an overall impression of the card. Answer each question on its own, about this card only; a card can look tidy and still fail exactly one of them.

- `example_meaning` — WRITE THIS FIRST, before any verdict: translate the example sentence into literal English, and name which German word it is actually demonstrating (watch for a separable prefix sitting at the end of the clause — "Er hielt das Buch **fest**" is demonstrating *festhalten*, not *halten*). Null only when there is no example. Do not skip this: the whole point is that you decide what the sentence means before you decide whether it matches the gloss.
- `gloss_sense_ok` — Now compare the meaning you just wrote against the gloss. Do they name the same sense of the same word? False for a false friend, for a homograph glossed with the wrong sense, and when the example demonstrates a different word. True when the card carries no example.
- `forms_ok` — Are the stated grammar facts real? Gender, plural, comparison forms, preposition case, and for verbs the past form and its auxiliary. True when the card states no such facts.
- `example_ok` — Is every example sentence grammatical, natural German that genuinely uses THIS headword? True when the card carries no example.
- `problem` — REQUIRED whenever any check is false: ONE short, specific English line naming exactly what is wrong (name the wrong form/word — this is fed straight back to the card writer). Null when all three pass.

Two rules for `problem`, because it is fed back verbatim and a wrong fix becomes the next wrong card:
- If a noun has NO plural, say "no plural" — never invent a plural form to replace another invented one.
- If you are sure something is wrong but not sure of the correct replacement, describe the error and say the right form must be checked. Never assert a replacement you are not confident in.
"""


def _facts(card: EnrichedCard) -> str:
    lines = [f"- type: {card.type}", f"- target: {card.target}"]
    if card.article:
        lines.append(f"- article: {card.article}")
    if card.reflexive:
        lines.append("- reflexive: true")
    if card.gloss:
        lines.append(f"- gloss: {card.gloss}")
    if card.note:
        lines.append(f"- note: {card.note}")
    if card.example:
        lines.append(f"- example: {card.example}")
    if card.past_form:
        lines.append(f"- past_form: {card.past_form}")
    if card.past_example:
        lines.append(f"- past_example: {card.past_example}")
    if card.level:
        lines.append(f"- level: {card.level}")
    return "\n".join(lines)


async def _verify_once(
    enriched: EnrichedCard,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
) -> CardVerdict:
    """One structured-output fact-check call over an already-forged card."""
    # temperature=0: this is a verdict, not prose (see structured_judge_llm).
    # It does NOT make the call deterministic — see _VERIFY_VOTES below.
    llm = structured_judge_llm(CardVerdict, temperature=0)
    prompt = PROMPT.replace("{facts}", _facts(enriched))
    with generation_span(
        "satz-forge-verify",
        model=VERIFIER_MODEL,
        input_text=prompt,
        user_id=user_id,
        session_id=session_id,
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result


# Two independent votes, and ANY rejection stands. Measured against the
# SATZ-021 audit set (16 production cards with a confirmed error, 40 audited
# clean): one call caught 44% of the bad cards, two caught 50%, and BOTH
# scored 0% false alarms on the clean 40. The two calls genuinely disagree —
# separate runs caught different cards — so with no false alarms to trade
# away, the second vote is free recall. They run concurrently, so this costs
# a call, not a second of latency, on a path the learner is waiting on.
_VERIFY_VOTES = 2


async def verify_card(
    enriched: EnrichedCard,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
) -> CardVerdict:
    """Fact-check an already-forged card (SATZ-021). Judges only — the retry
    loop lives in ``satz/routes.py``, which feeds a rejected ``problem`` back
    into the next ``enrich_word`` call so the retry converges instead of just
    resampling.

    Recall is the known weak point: this gate catches about half of the
    errors the offline audit found, which takes the forge's measured 10.1%
    error rate to roughly 5%. It does not replace the periodic sweep — it
    reduces what the sweep has to find.
    """
    votes = await asyncio.gather(
        *(
            _verify_once(enriched, user_id=user_id, session_id=session_id)
            for _ in range(_VERIFY_VOTES)
        ),
        return_exceptions=True,
    )
    verdicts = [v for v in votes if isinstance(v, CardVerdict)]
    if not verdicts:
        # Every leg failed — re-raise one so the caller's non-fatal handler
        # ships the card unverified rather than silently passing it.
        raise next(v for v in votes if isinstance(v, BaseException))
    for v in verdicts:
        if not v.ok:
            return v
    return verdicts[0]
