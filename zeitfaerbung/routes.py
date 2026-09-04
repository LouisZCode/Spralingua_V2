"""HTTP routes for Zeitfärbung (GRAM-003): fill a Präteritum blank by
choosing war / wurde / blieb by MEANING — English collapses these into
"was", German splits them semantically (state / onset-of-state / stayed-in-
state, plus the Vorgangspassiv "wurde + Partizip").

Deterministic grading only — NO judge LLM. The closed form set (war/warst/
waren/wart, wurde/wurdest/wurden/wurdet, blieb/bliebst/blieben/bliebt) is
small and unambiguous enough that string matching against ``item["answers"]``
is the whole grader; there is nothing here for an LLM to adjudicate.

v1: no personal items, no ledger-weighted round (unlike bauteil/verbindungen)
— just a balanced draw across the six content groups.
"""

import random

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.observability import propagate_trace_context, tracer
from auth.deps import get_current_user_id
from database.connection import get_db
from security import drill_try_admit
from database.repository import (
    credit_pattern_success,
    record_drill_attempt,
    record_grammar_error,
)
from drills import apply_level
from zeitfaerbung import grading
from zeitfaerbung.content import DOPPELDEUTIG_GROUPS, load_items

from coins.gate import admit_coins_or_402
from coins.prices import SATZ_ATTEMPT

router = APIRouter(prefix="/zeitfaerbung", tags=["zeitfaerbung"])

ROUND_SIZE = 10
# Balanced draw across the six content groups (v1: no personal items, no
# ledger weighting — "which meaning applies here?" is the whole drill, so a
# fixed spread across the pedagogy keeps every round representative).
_QUOTAS = {"zustand": 2, "uebergang": 2, "passiv": 2, "verbleib": 1}
_DOPPEL_QUOTA = 3


@router.get("/round")
async def get_round(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """One practice round; answers/hints/why/readings stay server-side until
    the verdict — only ``{id, frame, hint}`` ships, and ``hint`` is omitted
    entirely for doppeldeutig items (an English hint would force one reading
    and spoil the ambiguity that's the point of that group).

    LEVEL-001 is the only personalisation here: the quota selection below is
    otherwise identical for every learner. Note this drill is entirely A2/B1
    content, so an A1 learner is served NOTHING from it — deliberately. A
    beginner has no business on tense-aspect colouring, and the Flow simply
    deals them items from the drills that do have A1 content.
    """
    items = await apply_level(
        db, user_id=user_id, items=list(load_items().values()), drill="zeitfaerbung"
    )

    chosen: list[dict] = []
    used_ids: set[str] = set()
    for group, n in _QUOTAS.items():
        pool = [i for i in items if i["group"] == group]
        random.shuffle(pool)
        picks = pool[:n]
        chosen.extend(picks)
        used_ids.update(p["id"] for p in picks)

    doppel_pool = [i for i in items if i["group"] in DOPPELDEUTIG_GROUPS]
    random.shuffle(doppel_pool)
    doppel_picks = doppel_pool[:_DOPPEL_QUOTA]
    chosen.extend(doppel_picks)
    used_ids.update(p["id"] for p in doppel_picks)

    if len(chosen) < ROUND_SIZE:
        # Fill any shortfall (a thin group) from whatever's left, unweighted.
        remaining = [i for i in items if i["id"] not in used_ids]
        random.shuffle(remaining)
        chosen.extend(remaining[: ROUND_SIZE - len(chosen)])

    random.shuffle(chosen)

    result = []
    for i in chosen:
        entry = {"id": i["id"], "frame": i["frame"]}
        if i["group"] not in DOPPELDEUTIG_GROUPS:
            entry["hint"] = i["hint"]
        result.append(entry)
    return {"items": result}


class AttemptIn(BaseModel):
    item_id: str
    answer: str
    # OBS-007 practice-sitting id — same contract as the sibling drills.
    session_id: str | None = Field(None, max_length=64)
    # FLOW-002: the deliberate "give up" escape (Flow mode only) — skips
    # validation/recognition below and grades as a real, distinguishable miss.
    give_up: bool = False


@router.post("/attempts")
async def submit_attempt(
    body: AttemptIn,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Grade one typed war/wurde/blieb form deterministically, feed the
    ledger, and return the verdict (+ the meaning note — only now, it would
    answer the item beforehand)."""
    if not drill_try_admit(user_id):
        raise HTTPException(
            status_code=429,
            detail="You're going very fast — take a short break and try again in a few minutes.",
        )
    item = load_items().get(body.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item.")
    # PAY-002: AFTER the 404 but BEFORE grading/logging — stale id must not burn coins.
    # LEDGER-002: a give-up never reaches the recognizer — nothing to grade,
    # no provider budget spent — so it isn't charged either. Only a real,
    # graded attempt spends a coin.
    if not body.give_up:
        try:
            await admit_coins_or_402(db, user_id=user_id, price=SATZ_ATTEMPT, kind="spend_attempt")
        except HTTPException as _e:
            if _e.status_code == 402:
                raise
            raise HTTPException(status_code=503, detail="billing temporarily unavailable")
    answer = grading.normalize_answer(body.answer)
    # give_up skips validation entirely — there's nothing to type, the
    # learner is conceding the item.
    if not body.give_up:
        reason = grading.validate(item, answer)
        if reason:
            raise HTTPException(status_code=422, detail=reason)

    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("zeitfaerbung-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.observation.input", answer)

        if body.give_up:
            # FLOW-002 escape hatch: no recognizer runs — grade this exactly
            # like the "wrong verb entirely" branch below (same kind, same
            # note shape), so the frontend's existing wrong-verdict card
            # renders with no new branch. typed_for_ledger (grading.grade's
            # second return value) stays the literal gap marker — a real
            # attempt always substitutes a word here, so an unfilled "___"
            # in the ledger's sentence is itself the give-up sentinel
            # (DATA-004 review can tell the two apart).
            attempt_span.set_attribute("gave_up", True)

        verdict, typed_for_ledger = await grading.grade(item, answer, give_up=body.give_up)
        correct, kind, expected, note = (
            verdict["correct"],
            verdict["kind"],
            verdict["expected"],
            verdict["note"],
        )

        attempt_span.set_attribute(
            "langfuse.observation.output",
            f"correct={correct}" + (f" — {note}" if note else ""),
        )
        attempt_span.set_attribute("verdict.correct", bool(correct))

        # Feed the ledger (design rule 4) — non-fatal, same self-correcting
        # contract as bauteil/verbindungen: a drill-retired pattern that still
        # breaks in speech gets reopened by the spoken harvesters.
        # "unrecognized" input is NOT an attempt (the frontend keeps the item
        # live and unscored) — it must never heat the ledger or the DATA-004
        # log, so both writes are skipped for it.
        try:
            if kind == "unrecognized":
                pass
            elif correct:
                await credit_pattern_success(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    session_id=body.session_id,
                    source="zeitfaerbung",
                )
            elif not body.give_up:
                # LEDGER-002: a give-up carries no evidence of what the
                # learner would actually have typed — writing "the learner
                # broke this pattern" off a concession is a false row, and a
                # wrong ledger row is worse than a wrong verdict (CLAUDE.md).
                # record_drill_attempt below still logs the concession for
                # DATA-004's cross-drill view.
                expected_for_ledger = expected if expected is not None else item["answers"][0]
                await record_grammar_error(
                    db,
                    user_id=user_id,
                    pattern_id=item["pattern_id"],
                    sentence=item["frame"].replace("___", typed_for_ledger),
                    corrected=item["frame"].replace("___", expected_for_ledger),
                    note=note,
                    source="zeitfaerbung",
                    session_id=body.session_id,
                )
        except Exception:
            logger.exception(
                "Zeitfärbung ledger write failed (pattern {})", item["pattern_id"]
            )

        # Append to the cross-drill attempt log (DATA-004) — its own commit,
        # non-fatal like the ledger write above, same shape as bauteil.
        # Skipped for "unrecognized" for the same non-attempt reason.
        try:
            if kind != "unrecognized":
                await record_drill_attempt(
                    db,
                    user_id=user_id,
                    exercise="zeitfaerbung",
                    # ":giveup" suffix marks a concession in DATA-004 without
                    # a new column — same convention genus uses for its beats.
                    item_ref=item["id"] + (":giveup" if body.give_up else ""),
                    pattern_id=item["pattern_id"],
                    correct=correct,
                    modality="written",
                    session_id=body.session_id,
                )
        except Exception:
            logger.exception("Drill-attempt log write failed (item {})", item["id"])

        return verdict
