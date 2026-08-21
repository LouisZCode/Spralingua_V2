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
from database.repository import (
    credit_pattern_success,
    record_drill_attempt,
    record_grammar_error,
)
from drills import apply_level
from zeitfaerbung.content import (
    ALL_FORMS,
    DOPPELDEUTIG_GROUPS,
    FORM_TO_FAMILY,
    load_items,
)

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


# Learners naturally retype the whole sentence (the sibling drills support
# that), so the cap matches verbindungen's — the grader below pulls the one
# verb form out of a full retyped frame.
_MAX_ANSWER_CHARS = 120

_EDGE_PUNCT = " .,!?;:…\"'"


def _normalize(s: str) -> str:
    return " ".join(s.split()).strip(_EDGE_PUNCT).lower()


def _recognized_tokens(answer: str) -> list[str]:
    """Every token of the typed answer that is itself a war/wurde/blieb
    form — order preserved, duplicates kept (so "wurde wurde" correctly
    counts as two, triggering the "one verb form only" branch)."""
    normalized = _normalize(answer)
    tokens = (t.strip(_EDGE_PUNCT) for t in normalized.split())
    return [t for t in tokens if t in ALL_FORMS]


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
                detail="Keep it to one verb form — that looks like a paragraph.",
            )

    with propagate_trace_context(user_id=user_id, session_id=body.session_id), tracer.start_as_current_span("zeitfaerbung-attempt") as attempt_span:
        attempt_span.set_attribute("user.id", user_id)
        attempt_span.set_attribute("item_id", item["id"])
        if body.session_id:
            attempt_span.set_attribute("langfuse.session.id", body.session_id)
        attempt_span.set_attribute("langfuse.observation.input", answer)

        accepted = item["answers"]
        is_doppel = item["group"] in DOPPELDEUTIG_GROUPS
        reading = None
        alt = None

        if body.give_up:
            # FLOW-002 escape hatch: no recognizer runs — grade this exactly
            # like the "wrong verb entirely" branch below (same kind, same
            # note shape), so the frontend's existing wrong-verdict card
            # renders with no new branch. typed_for_ledger stays the literal
            # gap marker — a real attempt always substitutes a word here, so
            # an unfilled "___" in the ledger's sentence is itself the
            # give-up sentinel (DATA-004 review can tell the two apart).
            attempt_span.set_attribute("gave_up", True)
            correct, kind, expected = False, "verb", accepted[0]
            typed_for_ledger = "___"
            if is_doppel:
                sein_form = next(a for a in accepted if FORM_TO_FAMILY[a] == "sein")
                werden_form = next(a for a in accepted if FORM_TO_FAMILY[a] == "werden")
                note = (
                    f"Both forms work here: {sein_form} — "
                    f"{item['readings'][sein_form]}; {werden_form} — "
                    f"{item['readings'][werden_form]}."
                )
            else:
                note = item["why"]
        else:
            recognized = _recognized_tokens(answer)
            if len(recognized) > 1:
                # The learner retyped the sentence, and the FRAME itself may
                # contain war/wurde/blieb forms ("…, und die Straßen waren
                # leer."). Frame words are context, not the answer: discard
                # family forms up to their frame multiplicity and grade what
                # remains — but only when that leaves exactly one candidate
                # (a bare "war" typed alone never enters this branch).
                remaining = list(recognized)
                for frame_form in _recognized_tokens(item["frame"].replace("___", " ")):
                    if frame_form in remaining:
                        remaining.remove(frame_form)
                if len(remaining) == 1:
                    recognized = remaining

            if len(recognized) == 0:
                correct, kind, expected = False, "unrecognized", None
                note = "Answer with a form of war, wurde, or blieb."
                typed_for_ledger = answer
            elif len(recognized) > 1:
                correct, kind, expected = False, "unrecognized", None
                note = "One verb form only, please."
                typed_for_ledger = answer
            else:
                token = recognized[0]
                typed_for_ledger = token
                if token in accepted:
                    correct, kind, expected = True, "match", token
                    if is_doppel:
                        other = next(a for a in accepted if a != token)
                        reading = item["readings"][token]
                        alt = {"form": other, "reading": item["readings"][other]}
                        note = None
                    else:
                        note = item["why"]
                else:
                    token_family = FORM_TO_FAMILY.get(token)
                    family_match = next(
                        (a for a in accepted if FORM_TO_FAMILY.get(a) == token_family),
                        None,
                    )
                    if family_match is not None:
                        correct, kind, expected = False, "form", family_match
                        note = (
                            f"Right verb ({token_family}) — wrong form for this "
                            f"subject: {family_match}."
                        )
                    else:
                        correct, kind, expected = False, "verb", accepted[0]
                        if is_doppel:
                            sein_form = next(a for a in accepted if FORM_TO_FAMILY[a] == "sein")
                            werden_form = next(
                                a for a in accepted if FORM_TO_FAMILY[a] == "werden"
                            )
                            note = (
                                f"Two forms even work here: {sein_form} — "
                                f"{item['readings'][sein_form]}; {werden_form} — "
                                f"{item['readings'][werden_form]}. But {token} doesn't fit."
                            )
                        else:
                            note = item["why"]

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
            else:
                expected_for_ledger = expected if expected is not None else accepted[0]
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

        return {
            "correct": correct,
            "kind": kind,
            "expected": expected,
            # "unrecognized" keeps the item live for a retry — shipping the
            # accepted forms there would answer it in devtools.
            "accepted": accepted if kind != "unrecognized" else [],
            "note": note,
            "reading": reading,
            "alt": alt,
        }
