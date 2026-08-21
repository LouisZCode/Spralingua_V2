"""The "more German way" rephrase judge (IDIOM-002 Proposal-1).

Grades ONE learner line for idiomaticity — nothing else. The calibrated bar
is lifted from ``briefkasten/judge.py``'s ``natural_version`` section (the
one place this idea already shipped): rewrite only when a native would
genuinely phrase it differently, plain-but-native is the target, and null is
the common, correct outcome. Two hard exclusions keep it from becoming a
second grammar judge: transcription artifacts (the learner SPOKE most of
these lines — spelling is Deepgram's, not theirs) and grammar slips inside
natural phrasing (the drill judges and the debrief own those). Same Cerebras
``gpt-oss-120b`` wiring as every sibling judge.

The rewrite prompt is at v2 (2026-08-21, context-aware reframe); v1 is
archived at ``sim/prompt_versions/idiom_judge_v1.md``.
"""

from types import SimpleNamespace
from typing import Optional

from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm
# IDIOM-004 P1: the same conservative stem scan the Satzschmiede examiner
# uses to decide whether a spoken attempt used the card's word — reused here
# so "did the rewrite drop the practised word" is answered by ONE piece of
# inflection logic, not a second one that could disagree with the first.
from satz.examiner import _target_evidence

JUDGE_MODEL = "openai/gpt-oss-120b"


class IdiomRephrase(BaseModel):
    """One idiomaticity read of a learner's line. Phrasing only — never a
    grammar verdict, never a comment on spelling or punctuation."""

    natural: Optional[str] = Field(
        default=None,
        description=(
            "The line the way a German would REALLY say it in this "
            "register. MUST be null unless a native would genuinely phrase "
            "it differently — plain, simple German that a native would "
            "also produce gets null, not a polish"
        ),
    )
    # IDIOM-004 P2: a rewrite that says something different is not a
    # phrasing fix, it is a wrong answer with correct grammar. This is the
    # model's own self-check; `germanize` also runs a deterministic negation
    # scan afterward that does not depend on the model getting this right
    # (the *gilt -> gilt nicht* fixture).
    changes_meaning: bool = Field(
        description=(
            "True if the rewrite in `natural` adds, removes or negates any "
            "proposition, changes a tense, a person, or a quantity relative "
            "to their line — even a small one. False when `natural` is null, "
            "or when it says exactly the same thing, only phrased the way a "
            "German actually would"
        )
    )


_REGISTER_GUIDANCE = {
    "informal": (
        "everyday spoken German between people who know each other — the "
        "way people really talk, not textbook German"
    ),
    "formal": (
        "natural but polite Sie-register German — the way a German actually "
        "speaks to a stranger or writes to an office, still plain, never stiff"
    ),
}


# PROMPT v2 (2026-08-21) — context-aware reframe; v1 archived in sim/prompt_versions/idiom_judge_v1.md
PROMPT = """# Role
You are a native German speaker reading ONE line a German learner just produced. They produced it in a real exchange — your only question: would a German, responding in that same exchange, have phrased it this way? If yes, you stay silent. If not, you show how a German would really say it.

# Their line
"{text}"

# What they were responding to
{context}

# STEP 1 — read their line as a RESPONSE
The context above is what shapes how a German would naturally phrase the reply: a German answering a preference question answers in preference terms; one answering a why-question gives a reason the way a German gives a reason. Use the context to judge the FRAME their line should take — it is still someone else's words or the task they were given, NOT yours to rewrite or comment on. Everything you return is about the learner's line only. When the context is "(none given)", judge the line on its own, exactly as if there were no exchange at all.

The rewrite is still THEIR turn: same facts, same amount of content, their register — never a fuller or better answer than the one they actually gave.

- Question "Magst du deine Haare lieber kurz oder lang?", line "Ich mach meine Haare kurz" → natural: "Ich trage meine Haare lieber kurz." Answering a preference question about hair, a German says how they WEAR it ("tragen") and keeps the "lieber" of the preference — swapping "mach" for another verb inside the learner's frame ("Ich schneide meine Haare kurz") would say something else entirely (cutting it yourself) and miss how the answer is actually phrased.

# STEP 2 — this line usually comes from speech recognition
The learner most likely SPOKE it; the spelling on your screen is the recognizer's, not theirs. Lowercase nouns, missing umlauts, stray or missing punctuation, and homophone spellings (das/dass, seid/seit) are transcription artifacts. They NEVER count toward your decision and NEVER appear in your explanation.
- "ich habe diese woche viel zu tun" → natural: null. Lowercase is the recognizer's; the phrasing is exactly what a German says.

# STEP 3 — phrasing, not grammar
Other judges already grade grammar, and their verdict is sitting next to yours. You are not a grammar checker: never name a rule, never list corrections. The line between the two:
- A wrong ENDING, ARTICLE or VERB FORM inside a phrasing a German would use → null. The words are the right words; only their form slipped, and the grammar judge owns that.
- A wrong WORD CHOICE or PATTERN — the preposition, verb or construction English would pick, not German → rewrite. That is idiom, even when every ending is correct.

Worked examples:
- "Ich habe gestern mit meine Freundin Kaffee getrunken" → natural: null. "mit meine" is a case slip, but the PHRASING is exactly how a German says it — not your department.
- "Er hat mir mit den Hausaufgaben geholfen" → natural: "Er hat mir bei den Hausaufgaben geholfen." "mit" is English's *help with* wearing German clothes — Germans help "bei" something. Word choice, so it IS your department.
- CONTROL — "Ich bin glücklich zu hören, dass deine Prüfung gut war" → natural: "Schön, dass deine Prüfung gut gelaufen ist!" The grammar is fine; the phrasing is English wearing German words. THIS is your department.
- CONTROL — question "Warum bewerben Sie sich bei uns?", line "Ich habe Interesse an die Stelle, weil ich möchte neue Erfahrungen sammeln" → natural: null. "an die" is a case slip and "weil ich möchte ... sammeln" is verb order — both grammar territory. The PHRASING ("Interesse an der Stelle haben", a weil-reason) is exactly what a German answering this question says. Not your department.

When you rewrite, your German will naturally come out with correct endings even where theirs slipped — that is unavoidable and fine. The rewrite is ALL you return: it speaks for itself, side by side with their line. No commentary of any kind.

# STEP 4 — two things you may never do, no matter how much more German they'd sound
**Never change what was said.** Adding, dropping or flipping a negation, a tense, a person, a quantity — anything that makes the rewrite a different fact than the one they stated — is not a rephrase, it is a different sentence wearing their words.
- "Nee, mein Pass gilt." (their passport IS valid) is not "Nee, mein Pass gilt nicht." (it is NOT valid). If your first instinct produces something like this, that is `changes_meaning: true` and `natural` goes back to null — do not return the changed version.

**Never replace the word they were sent here to practise with a synonym you like better.** Two real lines about the same fact, seconds apart: "Meine Laufbahn läuft gut" and "meine Laufbahn verläuft gut" are BOTH already natural German — läuft and verläuft are equally correct here. Rewriting one into the other is not fixing phrasing, it is swapping their word for yours. Both get `natural: null`. Whatever word the learner reached for, right or not, stays in the rewrite unchanged — you may fix what is around it, never the word itself.

CONTROL — "Ich habe 30 Jahre" → natural: "Ich bin 30 Jahre alt." This one DOES get rewritten: stating age with "haben" is the English "I have 30 years" wearing German words, and a German always uses "sein" for age. Same fact, same size, genuinely a phrasing fix — STEP 4 is not blocking this one.

# `natural` — read this twice
Write one when the line is German that no native would produce — most often English translated word for word. These are exactly the cases to catch:
- "Ich bin glücklich zu hören, dass..." → a German says "Schön, dass..." or "Das freut mich."
- "Lass mich wissen, wann..." → "Sag mir Bescheid, wann..."
- "Ich frage mich, wie das Wetter ist" → "Wie ist denn das Wetter bei dir?"
- "Ich hatte eine sehr beschäftigte Woche" → "Ich hatte viel zu tun."
- "Ich werde dich besuchen kommen" → "Dann komme ich vorbei."

Return null when the line already sounds like German — simple, plain German counts as natural. Do NOT rewrite to add sophistication, vary word choice, or make it more interesting. Plain but native is the target, not impressive.

A rewrite that keeps their words and only changes the ORDER is not a rewrite — moving the right words around is grammar territory or taste, never phrasing. Return null instead.

The test is: would a German notice something is off in how it's PHRASED — as a response to what came before? If yes, rewrite. If they would simply hear it and answer, return null.

Keep the rewrite the same size and register as their line — one spoken turn, not a speech. Register: {register_guidance}
"""


def _normalized(s: str) -> str:
    """Casefold and strip everything but letters/digits — two lines that
    differ only in spacing, casing or punctuation are the same line."""
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def _token_multiset(s: str) -> list[str]:
    """The line's words, casefolded and order-blind. A 'rewrite' with the
    identical multiset only moved words around — that is grammar territory
    or taste, not phrasing advice, and counts as the null case."""
    return sorted(
        "".join(ch if ch.isalnum() else " " for ch in s.casefold()).split()
    )


# IDIOM-004 P2: negation markers, order-blind. A rewrite that adds or drops
# one of these relative to the input has flipped what was asserted — this
# check does not trust the model's own `changes_meaning` flag (the *gilt ->
# gilt nicht* fixture: the model returned the flip AND said nothing about
# it). `kein` is matched by prefix so keine/keinen/keinem/keiner/keines all
# count without listing every inflection.
_NEGATION_WORDS = {"nicht", "nie", "niemand", "niemanden", "niemandem", "nichts"}


def _negation_tokens(s: str) -> list[str]:
    tokens = "".join(ch if ch.isalnum() else " " for ch in s.casefold()).split()
    return sorted(t for t in tokens if t in _NEGATION_WORDS or t.startswith("kein"))


async def germanize(
    text: str,
    context: str | None,
    register: str = "informal",
    target: str | None = None,
) -> IdiomRephrase:
    """One structured-output rephrase call over the learner's line.

    ``target`` (IDIOM-004 P1) is the card/vocab word this line was supposed
    to practise, when the caller knows it (Satzschmiede does; a tandem
    bubble or Sprechen task does not). When given, a rewrite that drops
    every inflected/separated form of it is discarded — telling a learner
    the German way is to not use the word they came here to practise is
    worse than staying silent.
    """
    # temperature=0: the null/not-null decision is a verdict — the same line
    # must always get the same silence (see structured_judge_llm).
    llm = structured_judge_llm(IdiomRephrase, temperature=0)
    prompt = (
        PROMPT.replace("{text}", text)
        .replace("{context}", context or "(none given)")
        .replace(
            "{register_guidance}",
            _REGISTER_GUIDANCE.get(register, _REGISTER_GUIDANCE["informal"]),
        )
    )
    with generation_span("idiom-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    if result.natural is not None:
        candidate = result.natural.strip()
        # A whitespace-only rewrite, one identical to the input modulo
        # casing/punctuation, or one that merely REORDERS the learner's own
        # words IS the null case — enforced as code, not left to the prompt.
        discard = (
            not candidate
            or _normalized(candidate) == _normalized(text)
            or _token_multiset(candidate) == _token_multiset(text)
            # IDIOM-004 P2: the model flagged its own rewrite as a meaning
            # change — trust that over whatever the rewrite text looks like.
            or result.changes_meaning
            # IDIOM-004 P2: deterministic backstop, independent of the flag
            # above — a negation added or dropped is a different sentence.
            or _negation_tokens(candidate) != _negation_tokens(text)
        )
        if not discard and target:
            # IDIOM-004 P1: same stem scan the Satzschmiede examiner runs on
            # a spoken attempt, pointed at the rewrite instead. `card` only
            # needs the two attributes `_target_evidence` reads.
            #
            # Checked as a TRANSITION (present in their line, absent from
            # the rewrite) rather than a bare "absent from the rewrite":
            # the scan is built around present-tense/weak-verb stems and is
            # blind to ge-...-t/en participles ("abgeschlossen",
            # "beigetreten", "gelöscht" all read as "target absent" even
            # though the word IS there). Checking the transition means that
            # blind spot cancels out — it fires the same way on both sides
            # — and the guard only trips on a REAL drop, where the word was
            # detected going in and isn't coming out. Verified against the
            # 2026-08-15 trace dump: 4 of the day's genuine matches
            # (abschließen/vorziehen/löschen/beitreten, all participles)
            # were false-discarded by the bare check and are kept by this
            # one; the true drops (adj-aufregend -> spannend, überlegen ->
            # nachgedacht) still discard correctly.
            card = SimpleNamespace(target=target, tense_form=None)
            if _target_evidence(card, text) and not _target_evidence(card, candidate):
                discard = True
        if discard:
            result.natural = None
    return result
