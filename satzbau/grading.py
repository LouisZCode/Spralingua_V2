"""Pure validate + grade steps for Satzbau (CLARA-13), extracted verbatim
from ``satzbau/routes.py``'s ``POST /satzbau/attempts`` so the drill's own
route and ``teacher/registry.py``'s adapter share exactly one implementation.

Chip shuffling (``_shuffle_chips``) stays in ``satzbau/routes.py`` — it's a
serve-time concern, not a grading one, and the registry's ``serve()``
imports it from there directly (per CLARA-13's contract).

The drill's own route stays the only place that touches the DB (ledger
write, DATA-004 log) — this module is pure: no session, no writes, nothing
async except the judge call itself.
"""

from satzbau.judge import judge_clause

# Generous but bounded — the longest catalog item is 6 chips; this covers a
# real round plus headroom without accepting a garbage-sized payload.
_MAX_ORDER_TOKENS = 20
_MAX_TOKEN_CHARS = 40

_EDGE_PUNCT = " .,!?;:…\"'"


def _normalize_tokens(tokens: list[str]) -> tuple[str, ...]:
    return tuple(t.strip(_EDGE_PUNCT).lower() for t in tokens)


def _matches(order: list[str], item: dict) -> bool:
    """Exact match against the canonical answer, or against any listed
    ``accepts`` alternate. Deliberately narrower than the sibling drills'
    ``_matches`` (no substring/embedding heuristic needed) — the learner's
    submission is already a clean list of the exact chip tokens, not typed
    free text to fuzzily line up against a frame."""
    norm = _normalize_tokens(order)
    if norm == _normalize_tokens(item["answer"]):
        return True
    return any(norm == _normalize_tokens(alt) for alt in item["accepts"])


def validate(item: dict, order: list[str]) -> str | None:
    """422 reason, or ``None`` when ``order`` is acceptable to grade against
    ``item`` (satzbau/routes.py:288-297). Unlike the other four drills,
    satzbau's validate genuinely needs ``item`` — the chip-multiset check
    compares the submitted order against ``item["chips"]``."""
    if not order:
        return "Place the chips first."
    if len(order) > _MAX_ORDER_TOKENS or any(len(t) > _MAX_TOKEN_CHARS for t in order):
        return "That doesn't look like a chip set."
    if sorted(_normalize_tokens(order)) != sorted(_normalize_tokens(item["chips"])):
        return "Those aren't the chips you were given for this item."
    return None


async def grade(item: dict, order: list[str], *, give_up: bool = False) -> tuple[dict, bool]:
    """Returns ``(verdict, judge_skipped)``. ``verdict`` is EXACTLY the dict
    ``POST /satzbau/attempts`` returns (satzbau/routes.py:393-399);
    ``judge_skipped`` is True only when the deterministic match short-
    circuited the judge call."""
    variant: str | None = None
    judge_skipped = False
    if give_up:
        # FLOW-002 escape hatch: skip the judge entirely, grade as a miss,
        # and reveal the answer through the exact same response shape a
        # judged miss returns — no new frontend branch.
        correct, note = False, "Gave up — here's the order to learn."
    elif _matches(order, item):
        correct, note = True, None
        # A deterministic green never calls the LLM judge.
        judge_skipped = True
    elif (
        item.get("must_start_with")
        and order
        and _normalize_tokens([order[0]])[0] != item["must_start_with"].strip().lower()
    ):
        # FIX A (CLARA-14 Phase B verification, BLOCKER): the five
        # v2-wortstellung tasks NAME the word that must open the sentence.
        # An otherwise-grammatical order that fronts something else (most
        # often the bare subject) is still wrong for THIS task, and the
        # judge was measured accepting it — so this is enforced
        # deterministically, before any judge call, same as the multiset
        # match above. Give-up and the deterministic-green path above are
        # untouched.
        opener = item["must_start_with"]
        correct, note = (
            False,
            f"The task asks you to start with '{opener[:1].upper()}{opener[1:]}'.",
        )
        judge_skipped = True
    else:
        diag = await judge_clause(item, order)
        correct, note, variant = diag.correct, diag.note, diag.variant

    return (
        {
            "correct": correct,
            "expected": item["answer"],
            "rule": item["rule"],
            "note": note,
            "variant": variant,
            # GRAM-009: lets the frontend fetch GET /grammar/pattern/{id}
            # for the collapsed "Warum?" disclosure under the verdict card.
            # camelCase like every other practice payload in this repo.
            "patternId": item["pattern_id"],
        },
        judge_skipped,
    )
