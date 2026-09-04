"""Pure validate + grade steps for Feste Verbindungen (CLARA-13), extracted
verbatim from ``verbindungen/routes.py``'s ``POST /verbindungen/attempts`` so
the drill's own route and ``teacher/registry.py``'s adapter share exactly one
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

from verbindungen.judge import judge_chunk

_MAX_ANSWER_CHARS = 120  # verbindungen/routes.py's own cap

# Same deterministic-match contract as bauteil/routes.py (kept local — each
# exercise module is self-contained like satz/ and bauteil/ are).
_EDGE_PUNCT = " .,!?;:…\"'"


def normalize_answer(answer: str) -> str:
    """Collapse whitespace exactly as the route does before validating or
    grading (verbindungen/routes.py:319)."""
    return " ".join(answer.split())


def _normalize(s: str) -> str:
    # BUG-011: contractions expand to their two-word form, so "beim" and
    # "bei dem" compare equal. The case distinction survives (im -> in dem
    # vs. ins -> in das), so this cannot green a wrong case.
    return expand_contractions(" ".join(s.split()).strip(_EDGE_PUNCT).lower())


def _matches(typed: str, expected: str, frame: str) -> bool:
    """Exact match, or the answer embedded in the typed-out sentence — but
    ONLY frame words may surround it. Plain containment would defeat the
    decoys: "mich auf" contains the decoy answer "auf", yet the extra
    pronoun is exactly the error the mix exists to catch — it must reach
    the judge, never green deterministically."""
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
    as the route today (verbindungen/routes.py:322-329)."""
    if not answer:
        return "Type your answer first."
    if len(answer) > _MAX_ANSWER_CHARS:
        return "Keep it to the missing words — that looks like a paragraph."
    return None


async def grade(item: dict, answer: str, *, give_up: bool = False) -> tuple[dict, bool]:
    """Returns ``(verdict, judge_skipped)``. ``verdict`` is EXACTLY the dict
    ``POST /verbindungen/attempts`` returns (verbindungen/routes.py:423-428);
    ``judge_skipped`` is True only when the deterministic match short-
    circuited the judge call."""
    judge_skipped = False
    if give_up:
        # FLOW-002 escape hatch: skip the judge entirely, grade as a miss,
        # and reveal the canonical chunk through the exact same response
        # shape a judged miss returns — no new frontend branch.
        correct, note = False, "Gave up — here's the chunk to learn."
    elif _matches(answer, item["answer"], item["frame"]):
        correct, note = True, None
        # A deterministic green never calls the LLM judge.
        judge_skipped = True
    else:
        diag = await judge_chunk(item, answer)
        correct, note = diag.correct, diag.note

    return (
        {
            "correct": correct,
            "expected": item["answer"],
            "chunk": item["chunk"],
            "note": note,
            # GRAM-009: lets the frontend fetch GET /grammar/pattern/{id}
            # for the collapsed "Warum?" disclosure under the verdict card.
            # camelCase like every other practice payload in this repo.
            "patternId": item["pattern_id"],
        },
        judge_skipped,
    )
