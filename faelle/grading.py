"""Pure validate + grade steps for Fälle (CLARA-13), extracted verbatim from
``faelle/routes.py``'s ``POST /faelle/attempts`` so the drill's own route and
``teacher/registry.py``'s adapter share exactly one implementation — neither
re-derives the deterministic match or the verdict shape independently.

The drill's own route stays the only place that touches the DB (ledger write,
DATA-004 log) — this module is pure: no session, no writes, nothing async
except the judge call itself.
"""

import re

from grammar import expand_contractions

from faelle.judge import judge_case

_MAX_ANSWER_CHARS = 120  # faelle/routes.py's own cap

# Same deterministic-match contract as verbindungen/bauteil (kept local —
# each exercise module is self-contained like satz/ and bauteil/ are).
_EDGE_PUNCT = " .,!?;:…\"'"


def normalize_answer(answer: str) -> str:
    """Collapse whitespace exactly as the route does before validating or
    grading (faelle/routes.py:265)."""
    return " ".join(answer.split())


def _normalize(s: str) -> str:
    # BUG-011: contractions expand to their two-word form, so "beim" and
    # "bei dem" compare equal. Expansion keeps dative and accusative apart
    # (im -> in dem vs. ins -> in das), so the wechselpraepositionen pairs
    # warned about below are still safe — a genuine case swap stays red.
    return expand_contractions(" ".join(s.split()).strip(_EDGE_PUNCT).lower())


def _matches(typed: str, expected: str, frame: str) -> bool:
    """Exact match, or the answer embedded in the typed-out sentence — but
    ONLY frame words may surround it. Plain containment would defeat the
    wechselpraepositionen pairs: "Tisch" is common to both "auf den Tisch"
    and "auf dem Tisch", so a lax match could green the wrong case outright
    — a genuine case swap must reach the judge, never green deterministically."""
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
    for signature uniformity with satzbau's ``validate`` (which needs it for
    the chip-multiset check) — unused here, same as the route today, which
    never consults the item at this step (faelle/routes.py:268-275)."""
    if not answer:
        return "Type your answer first."
    if len(answer) > _MAX_ANSWER_CHARS:
        return "Keep it to the missing words — that looks like a paragraph."
    return None


async def grade(item: dict, answer: str, *, give_up: bool = False) -> tuple[dict, bool]:
    """Returns ``(verdict, judge_skipped)``. ``verdict`` is EXACTLY the dict
    ``POST /faelle/attempts`` returns (faelle/routes.py:369-375);
    ``judge_skipped`` is True only when the deterministic match short-
    circuited the judge call — the route uses it to stamp the same
    ``judge_skipped`` span attribute it always has. ``gave_up`` stays the
    route's own concern (it comes from the request, not from here)."""
    means_instead: str | None = None
    judge_skipped = False
    if give_up:
        # FLOW-002 escape hatch: skip the judge entirely, grade as a miss,
        # and reveal the rule through the exact same response shape a
        # judged miss returns — no new frontend branch.
        correct, note = False, "Gave up — here's the rule to learn."
    elif _matches(answer, item["answer"], item["frame"]):
        correct, note = True, None
        # A deterministic green never calls the LLM judge.
        judge_skipped = True
    else:
        diag = await judge_case(item, answer)
        correct, note, means_instead = diag.correct, diag.note, diag.means_instead

    return (
        {
            "correct": correct,
            "expected": item["answer"],
            "rule": item["rule"],
            "note": note,
            "meansInstead": means_instead,
        },
        judge_skipped,
    )
