"""Pure validate + grade steps for Bauteil-Sätze (CLARA-13), extracted
verbatim from ``bauteil/routes.py``'s ``POST /bauteil/attempts`` so the
drill's own route and ``teacher/registry.py``'s adapter share exactly one
implementation.

The drill's own route stays the only place that touches the DB (ledger
write, DATA-004 log, and the CONT-002 personal-item lookup fallback for a
stale generic id) — this module is pure and catalog-agnostic: no session,
no writes, nothing async except the judge call itself. The teacher path
deliberately stays catalog-only (see ``teacher/registry.py``'s docstring) —
this module never reaches for ``UserDrillItem`` itself either way.
"""

import re

from grammar import expand_contractions

from bauteil.judge import judge_attempt

# Learners type one phrase; anything longer than a generous full sentence is
# a paste accident, not an attempt.
_MAX_ANSWER_CHARS = 120

_EDGE_PUNCT = " .,!?;:…\"'"


def normalize_answer(answer: str) -> str:
    """Collapse whitespace exactly as the route does before validating or
    grading (bauteil/routes.py:208)."""
    return " ".join(answer.split())


def _normalize(s: str) -> str:
    # BUG-011: contractions expand to their two-word form, so "beim" and
    # "bei dem" compare equal. The case distinction survives (im -> in dem
    # vs. ins -> in das), so this cannot green a wrong declension.
    return expand_contractions(" ".join(s.split()).strip(_EDGE_PUNCT).lower())


def _matches(typed: str, expected: str, frame: str) -> bool:
    """Deterministic green: the exact phrase, or the phrase embedded in the
    typed-out sentence. Containment alone would be a hole — extra NON-frame
    words around the phrase (an added pronoun, a different preposition) must
    fall through to the judge — so whatever surrounds the match must consist
    of frame words only (i.e. the learner typed the sentence, nothing else)."""
    t, e = _normalize(typed), _normalize(expected)
    if t == e:
        return True
    m = re.search(rf"(?<!\w){re.escape(e)}(?!\w)", t)
    if m is None:
        return False
    frame_words = set(_normalize(frame.replace("___", " ")).split())
    leftover = (t[: m.start()] + " " + t[m.end():]).split()
    return all(w in frame_words for w in leftover)


def validate(item: dict, answer: str) -> str | None:
    """422 reason, or ``None`` when ``answer`` (already whitespace-normalized
    via ``normalize_answer``) is acceptable to grade. ``item`` is accepted
    for signature uniformity with satzbau's ``validate`` — unused here, same
    as the route today (bauteil/routes.py:211-217)."""
    if not answer:
        return "Type your answer first."
    if len(answer) > _MAX_ANSWER_CHARS:
        return "Keep it to the phrase — that looks like a paragraph."
    return None


async def grade(item: dict, answer: str, *, give_up: bool = False) -> tuple[dict, bool]:
    """Returns ``(verdict, judge_skipped)``. ``verdict`` is EXACTLY the dict
    ``POST /bauteil/attempts`` returns (bauteil/routes.py:330-337) — the
    camelCase ``caseOk``/``carrierOk`` axis booleans are load-bearing
    (AxisPill chips), preserved exactly. ``judge_skipped`` is True only when
    the deterministic match short-circuited the judge call."""
    judge_skipped = False
    if give_up:
        # FLOW-002 escape hatch: skip the judge entirely, grade as a miss,
        # and reveal the gold phrase through the exact same response shape
        # a judged miss returns — the frontend needs no new branch to show
        # it.
        correct, case_ok, carrier_ok, note = (
            False,
            False,
            False,
            "Gave up — here's the phrase to learn.",
        )
    elif _matches(answer, item["answer"], item["frame"]):
        correct, case_ok, carrier_ok, note = True, True, True, None
        # A deterministic green never calls the LLM judge.
        judge_skipped = True
    else:
        diag = await judge_attempt(item, answer)
        correct, case_ok, carrier_ok, note = (
            diag.correct,
            diag.case_ok,
            diag.carrier_ok,
            diag.note,
        )

    return (
        {
            "correct": correct,
            "expected": item["answer"],
            "caseOk": case_ok,
            "carrierOk": carrier_ok,
            "note": note,
        },
        judge_skipped,
    )
