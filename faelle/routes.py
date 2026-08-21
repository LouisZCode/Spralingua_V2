"""HTTP routes for Fälle — interleaved German case drill (GRAM-006
Proposal-1, "the case cluster": the one two-way preposition group, the two
fixed-case preposition groups, the direct-object article, the small
dative-only verb group, and the personal-pronoun case split).

Same shape as ``verbindungen/routes.py``: ledger-weighted round, deterministic
check first, one diagnosis call on misses, non-fatal ledger feedback, OBS-007
tracing under the frontend-minted practice-session id. Unlike verbindungen,
this is generic-catalog only (v1) — no CONT-002 personal-forge path.
"""

import random
import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import propagate_trace_context, tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from database.repository import (
    credit_pattern_success,
    load_grammar_focus,
    record_drill_attempt,
    record_grammar_error,
)
from drills import apply_level
from faelle.content import TARGET_PATTERNS, load_items
from faelle.judge import judge_case
from grammar import expand_contractions
from security import drill_try_admit

router = APIRouter(prefix="/faelle", tags=["faelle"])

ROUND_SIZE = 10
# Hot patterns lead but never fill the round on their own — the cap below is
# the other half of the mechanism that keeps the six patterns interleaved.
MAX_HOT = 5

# THE INTERLEAVING RULE — this is the exercise's entire mechanism, enforced
# as code, not left as a hope. No single pattern may account for more than a
# third of a round, and a round must force at least four distinct case
# decisions. Without this, a ledger-weighted round can legitimately hand
# back 6+ Wechselpräposition items in a row (it's the learner's hottest
# pattern) — and a learner who sees three of those consecutively stops
# deciding WHICH rule applies and just answers "wohin oder wo?" on
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
    "three Wechselpräposition items back to back, then everything else" if
    left in selection order. The whole point of Fälle is forcing the learner
    to re-decide which case rule applies on EVERY item — a clump lets them
    coast on the previous item's rule instead of actually deciding.
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


@router.get("/round")
async def get_round(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One practice round: ledger-weighted selection, then capped +
    interleaved so the MIX — not any one hot pattern — stays the mechanism.
    Answers/rules stay server-side (the rule line would answer the item, so
    it only ships with the verdict).
    """
    # LEVEL-001: narrow before the ledger weighting below.
    items = await apply_level(
        db, user_id=user_id, items=list(load_items().values()), drill="faelle"
    )
    try:
        focus = await load_grammar_focus(db, user_id=user_id, limit=10)
        hot = {f["pattern_id"] for f in focus} & set(TARGET_PATTERNS)
    except Exception:
        logger.exception("Fälle focus read failed — serving an unweighted round")
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

    result = [{"id": i["id"], "frame": i["frame"], "hint": i["hint"]} for i in ordered]
    return {"items": result}


_MAX_ANSWER_CHARS = 120

# Same deterministic-match contract as verbindungen/routes.py (kept local —
# each exercise module is self-contained like satz/ and bauteil/ are).
_EDGE_PUNCT = " .,!?;:…\"'"


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


class AttemptIn(BaseModel):
    item_id: str
    answer: str
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
    """Judge one typed case gap-fill, feed the ledger, return the verdict +
    the rule to learn (only now — it would answer the item)."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    item = load_items().get(body.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    answer = " ".join(body.answer.split())
    # give_up skips validation entirely — there's nothing to type, the
    # learner is conceding the item.
    if not body.give_up:
        if not answer:
            raise HTTPException(status_code=422, detail="Type your answer first.")
        if len(answer) > _MAX_ANSWER_CHARS:
            raise HTTPException(
                status_code=422,
                detail="Keep it to the missing words — that looks like a paragraph.",
            )

    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("faelle-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.observation.input", answer)

        means_instead: str | None = None
        if body.give_up:
            # FLOW-002 escape hatch: skip the judge entirely, grade as a
            # miss, and reveal the rule through the exact same response
            # shape a judged miss returns — no new frontend branch.
            attempt_span.set_attribute("gave_up", True)
            correct, note = False, "Gave up — here's the rule to learn."
        elif _matches(answer, item["answer"], item["frame"]):
            correct, note = True, None
            # A deterministic green never calls the LLM judge — flag that
            # explicitly so Langfuse can tell "judged correct" apart from
            # "matched without a judge call" (same contract as bauteil/
            # verbindungen).
            attempt_span.set_attribute("judge_skipped", True)
        else:
            try:
                diag = await judge_case(item, answer)
            except Exception as exc:
                attempt_span.record_exception(exc)
                logger.exception("Fälle judge call failed (item {})", item["id"])
                raise HTTPException(
                    status_code=502,
                    detail="The judge is unavailable right now — try again in a moment.",
                )
            correct, note, means_instead = diag.correct, diag.note, diag.means_instead

        attempt_span.set_attribute(
            "langfuse.observation.output",
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
                    source="faelle",
                )
            else:
                await record_grammar_error(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    # A give-up never typed an answer — leave the gap
                    # unfilled rather than substitute an empty string; the
                    # literal "___" is the give-up sentinel in the ledger.
                    sentence=(
                        item["frame"]
                        if body.give_up
                        else item["frame"].replace("___", answer)
                    ),
                    corrected=item["frame"].replace("___", item["answer"]),
                    note=note,
                    source="faelle",
                    session_id=body.session_id,
                )
        except Exception:
            logger.exception("Fälle ledger write failed (pattern {})", item["pattern_id"])

        # Append to the cross-drill attempt log (DATA-004) — its own commit,
        # non-fatal like the ledger write above, same shape as verbindungen.
        try:
            await record_drill_attempt(
                db,
                user_id=user_id,
                exercise="faelle",
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
            "meansInstead": means_instead,
        }
