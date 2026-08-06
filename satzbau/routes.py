"""HTTP routes for Satzbau — German clause-construction drill (order chips
into a correct subordinate clause or question). Covers five taxonomy
patterns that are all the same underlying problem: build the clause, then
put the verb where German puts it — relativsatz, indirekte-frage,
zu-infinitiv, um-zu-damit, fragen-wortstellung.

Same shape as ``faelle/routes.py``: ledger-weighted round, deterministic
check first, one diagnosis call on misses, non-fatal ledger feedback, OBS-007
tracing under the frontend-minted practice-session id. Generic-catalog only
— no CONT-002 personal-forge path.
"""

import random
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.repository import (
    credit_pattern_success,
    load_grammar_focus,
    record_drill_attempt,
    record_grammar_error,
)
from satzbau.content import TARGET_PATTERNS, load_items
from satzbau.judge import judge_clause
from security import drill_try_admit

router = APIRouter(prefix="/satzbau", tags=["satzbau"])

ROUND_SIZE = 10
# Hot patterns lead but never fill the round on their own — the cap below is
# the other half of the mechanism that keeps the five patterns interleaved.
MAX_HOT = 5

# THE INTERLEAVING RULE — same mechanism as faelle/routes.py, same reasoning:
# no single pattern may account for more than a third of a round, and a
# round must force at least four of the five distinct clause structures.
# Without this, a ledger-weighted round can legitimately hand back 6+
# relativsatz items in a row (it's the learner's hottest pattern) — and a
# learner who sees three of those consecutively stops deciding WHICH
# structure is being asked for (relative pronoun? indirect question? um…zu
# vs damit? a direct question?) and just answers "verb goes at the end" on
# autopilot, which is exactly the failure mode this drill exists to break.
MAX_PER_PATTERN = 3
MIN_PATTERNS = 4


def _cap_and_diversify(chosen: list[dict], pool: list[dict]) -> list[dict]:
    """Enforce MAX_PER_PATTERN and MIN_PATTERNS on a selected round, topping
    up from ``pool`` (the full catalog) when the initial ledger-weighted
    selection over-indexes on too few patterns.

    The cap is enforced on every single insertion below — including during
    top-up — not just on the initial selection. A top-up pass that only
    checked the STARTING counts would happily push a pattern back over
    MAX_PER_PATTERN by filling leftover slots with more of it once other
    patterns run dry, silently defeating the whole cap.
    """
    chosen_ids = {i["id"] for i in chosen}
    counts: Counter = Counter()
    kept: list[dict] = []
    kept_ids: set[str] = set()
    for item in chosen:
        pid = item["pattern_id"]
        if counts[pid] < MAX_PER_PATTERN:
            kept.append(item)
            kept_ids.add(item["id"])
            counts[pid] += 1

    target = len(chosen)
    reserve = [i for i in pool if i["id"] not in chosen_ids]
    random.shuffle(reserve)

    def _fill(candidates: list[dict]) -> None:
        for c in candidates:
            if len(kept) >= target:
                return
            pid = c["pattern_id"]
            if c["id"] in kept_ids or counts[pid] >= MAX_PER_PATTERN:
                continue
            kept.append(c)
            kept_ids.add(c["id"])
            counts[pid] += 1

    # Prefer patterns not yet represented first (pushes toward
    # MIN_PATTERNS without needing the swap loop below), then anything else
    # still under cap — a smaller/uneven catalog may still land short of
    # `target`, which beats silently breaking the cap to hit the count.
    _fill([i for i in reserve if counts[i["pattern_id"]] == 0])
    _fill(reserve)

    # Still short of MIN_PATTERNS distinct ids (small/uneven catalog): swap
    # one item out of the currently-fattest pattern for an unrepresented
    # one, repeating until the floor is met or candidates run out.
    distinct = {p for p, n in counts.items() if n > 0}
    if len(distinct) < MIN_PATTERNS:
        candidates = [
            i for i in pool if i["pattern_id"] not in distinct and i["id"] not in kept_ids
        ]
        random.shuffle(candidates)
        for c in candidates:
            if len(distinct) >= MIN_PATTERNS:
                break
            fattest = counts.most_common(1)[0][0]
            for idx, i in enumerate(kept):
                if i["pattern_id"] == fattest:
                    kept[idx] = c
                    kept_ids.discard(i["id"])
                    kept_ids.add(c["id"])
                    counts[fattest] -= 1
                    counts[c["pattern_id"]] += 1
                    distinct.add(c["pattern_id"])
                    break
    return kept


def _order_no_adjacent_repeats(items: list[dict]) -> list[dict]:
    """Greedy reorder so no two consecutive items share a pattern_id,
    whenever the multiset allows it — repeatedly place the most-common
    remaining pattern that isn't the one just placed.

    This is the ordering half of THE INTERLEAVING RULE (see MAX_PER_PATTERN
    above): even a round that respects the per-pattern cap can still clump
    "three relativsatz items back to back, then everything else" if left in
    selection order. The whole point of Satzbau is forcing the learner to
    re-decide which structure is being asked for on EVERY item — a clump
    lets them coast on the previous item's structure instead of actually
    deciding.
    """
    buckets: dict[str, list[dict]] = {}
    for item in items:
        buckets.setdefault(item["pattern_id"], []).append(item)
    for bucket in buckets.values():
        random.shuffle(bucket)

    ordered: list[dict] = []
    last_pattern: str | None = None
    while any(buckets.values()):
        candidates = [p for p, b in buckets.items() if b and p != last_pattern]
        if not candidates:
            # Only the just-placed pattern has items left — an unavoidable
            # repeat (never happens with MAX_PER_PATTERN=3/ROUND_SIZE=10,
            # kept as a safety net for a smaller/uneven catalog).
            candidates = [p for p, b in buckets.items() if b]
        # Most-common remaining pattern first, so a big bucket never gets
        # stranded at the end where it has no choice but to repeat itself.
        candidates.sort(key=lambda p: len(buckets[p]), reverse=True)
        chosen_pattern = candidates[0]
        ordered.append(buckets[chosen_pattern].pop())
        last_pattern = chosen_pattern
    return ordered


def _shuffle_chips(chips: list[str], answer: list[str]) -> list[str]:
    """Shuffle a copy of ``chips`` for serving — re-rolling if it lands on
    the answer order (a shuffle that hands back the solution isn't a
    puzzle). A handful of retries is plenty for the catalog's item sizes
    (2-6 chips); the loop still terminates on a degenerate 0/1-chip or
    all-identical-token item instead of spinning forever."""
    shuffled = list(chips)
    if len(shuffled) <= 1:
        return shuffled
    for _ in range(20):
        random.shuffle(shuffled)
        if shuffled != answer:
            return shuffled
    return shuffled


@router.get("/round")
async def get_round(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One practice round: ledger-weighted selection, then capped +
    interleaved so the MIX — not any one hot pattern — stays the mechanism.
    Chips are shuffled per serve; ``answer``/``accepts``/``rule`` stay
    server-side (the rule line would answer the item, so it only ships with
    the verdict).
    """
    items = list(load_items().values())
    try:
        focus = await load_grammar_focus(db, user_id=user_id, limit=10)
        hot = {f["pattern_id"] for f in focus} & set(TARGET_PATTERNS)
    except Exception:
        logger.exception("Satzbau focus read failed — serving an unweighted round")
        hot = set()

    hot_items = [i for i in items if i["pattern_id"] in hot]
    rest = [i for i in items if i["pattern_id"] not in hot]
    random.shuffle(hot_items)
    random.shuffle(rest)
    chosen = (hot_items[:MAX_HOT] + rest)[:ROUND_SIZE]
    if len(chosen) < ROUND_SIZE:
        chosen += hot_items[MAX_HOT : MAX_HOT + ROUND_SIZE - len(chosen)]

    chosen = _cap_and_diversify(chosen, items)
    ordered = _order_no_adjacent_repeats(chosen)

    result = [
        {
            "id": i["id"],
            "given": i["given"],
            "task": i["task"],
            "chips": _shuffle_chips(i["chips"], i["answer"]),
            "hint": i["hint"],
        }
        for i in ordered
    ]
    return {"items": result}


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


class AttemptIn(BaseModel):
    item_id: str
    order: list[str]
    # OBS-007 practice-sitting id — same contract as the sibling drills.
    session_id: str | None = Field(None, max_length=64)
    # FLOW-002: the deliberate "give up" escape (Flow mode only) — skips
    # validation/judging below and grades as a real, distinguishable miss.
    give_up: bool = False


@router.post("/attempts")
async def submit_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Judge one built chip order, feed the ledger, return the verdict + the
    canonical order to learn (only now — it would answer the item)."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    item = load_items().get(body.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")

    order = body.order
    # give_up skips validation entirely — there's nothing built, the
    # learner is conceding the item.
    if not body.give_up:
        if not order:
            raise HTTPException(status_code=422, detail="Place the chips first.")
        if len(order) > _MAX_ORDER_TOKENS or any(len(t) > _MAX_TOKEN_CHARS for t in order):
            raise HTTPException(status_code=422, detail="That doesn't look like a chip set.")
        if sorted(_normalize_tokens(order)) != sorted(_normalize_tokens(item["chips"])):
            raise HTTPException(
                status_code=422,
                detail="Those aren't the chips you were given for this item.",
            )

    with tracer.start_as_current_span("satzbau-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.trace.input", " ".join(order))

        variant: str | None = None
        if body.give_up:
            # FLOW-002 escape hatch: skip the judge entirely, grade as a
            # miss, and reveal the answer through the exact same response
            # shape a judged miss returns — no new frontend branch.
            attempt_span.set_attribute("gave_up", True)
            correct, note = False, "Gave up — here's the order to learn."
        elif _matches(order, item):
            correct, note = True, None
            # A deterministic green never calls the LLM judge — flag that
            # explicitly so Langfuse can tell "judged correct" apart from
            # "matched without a judge call" (same contract as the sibling
            # drills).
            attempt_span.set_attribute("judge_skipped", True)
        else:
            try:
                diag = await judge_clause(item, order)
            except Exception as exc:
                attempt_span.record_exception(exc)
                logger.exception("Satzbau judge call failed (item {})", item["id"])
                raise HTTPException(
                    status_code=502,
                    detail="The judge is unavailable right now — try again in a moment.",
                )
            correct, note, variant = diag.correct, diag.note, diag.variant

        attempt_span.set_attribute(
            "langfuse.trace.output",
            f"correct={correct}" + (f" — {note}" if note else ""),
        )
        # Structured verdict attribute so Langfuse can filter without
        # string-parsing the free-text trace.output above.
        attempt_span.set_attribute("verdict.correct", bool(correct))

        # Feed the ledger (design rule 4) — non-fatal, same self-correcting
        # contract as the sibling drills: a drill-retired pattern that still
        # breaks in speech gets reopened by the spoken harvesters.
        try:
            if correct:
                await credit_pattern_success(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    session_id=body.session_id,
                    source="satzbau",
                )
            else:
                await record_grammar_error(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    # A give-up never built an order — leave the built
                    # sentence unfilled rather than substitute an empty
                    # string; the bare lead-in is the give-up sentinel in
                    # the ledger.
                    sentence=(
                        item["given"]
                        if body.give_up
                        else " ".join([item["given"], *order]).strip()
                    ),
                    corrected=" ".join([item["given"], *item["answer"]]).strip(),
                    note=note,
                    source="satzbau",
                    session_id=body.session_id,
                )
        except Exception:
            logger.exception("Satzbau ledger write failed (pattern {})", item["pattern_id"])

        # Append to the cross-drill attempt log (DATA-004) — its own commit,
        # non-fatal like the ledger write above, same shape as the sibling
        # drills.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="satzbau",
                # ":giveup" suffix marks a concession in DATA-004 without a
                # new column — same convention the sibling drills use.
                item_ref=item["id"] + (":giveup" if body.give_up else ""),
                pattern_id=item["pattern_id"],
                correct=correct,
                modality="written",
                session_id=body.session_id,
            )
        except Exception:
            logger.exception("Drill-attempt log write failed (item {})", item["id"])

        return {
            "correct": correct,
            "expected": item["answer"],
            "rule": item["rule"],
            "note": note,
            "variant": variant,
        }
