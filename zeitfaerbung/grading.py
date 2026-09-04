"""Pure grade step for Zeitfärbung (CLARA-13), extracted verbatim from
``zeitfaerbung/routes.py``'s ``POST /zeitfaerbung/attempts`` so the drill's
own route and ``teacher/registry.py``'s adapter share exactly one
implementation.

Deterministic only — NO judge LLM (same as the route's own docstring
explains): the closed war/wurde/blieb form set is small and unambiguous
enough that recognizing the typed token against ``item["answers"]`` is the
whole grader. ``grade`` is still declared ``async`` for interface parity with
the other four drills' grading modules (so ``teacher/registry.py``'s adapter
dataclass can await every drill's ``grade`` uniformly) even though nothing
here actually awaits.

The drill's own route stays the only place that touches the DB (ledger
write, DATA-004 log) and the write-skip on ``kind == "unrecognized"`` — this
module is pure: no session, no writes.
"""

from zeitfaerbung.content import ALL_FORMS, DOPPELDEUTIG_GROUPS, FORM_TO_FAMILY

# Learners naturally retype the whole sentence (the sibling drills support
# that), so the cap matches verbindungen's — the grader below pulls the one
# verb form out of a full retyped frame.
_MAX_ANSWER_CHARS = 120

_EDGE_PUNCT = " .,!?;:…\"'"


def normalize_answer(answer: str) -> str:
    """Collapse whitespace exactly as the route does before validating or
    grading (zeitfaerbung/routes.py:159)."""
    return " ".join(answer.split())


def _normalize(s: str) -> str:
    return " ".join(s.split()).strip(_EDGE_PUNCT).lower()


def _recognized_tokens(answer: str) -> list[str]:
    """Every token of the typed answer that is itself a war/wurde/blieb
    form — order preserved, duplicates kept (so "wurde wurde" correctly
    counts as two, triggering the "one verb form only" branch)."""
    normalized = _normalize(answer)
    tokens = (t.strip(_EDGE_PUNCT) for t in normalized.split())
    return [t for t in tokens if t in ALL_FORMS]


def validate(item: dict, answer: str) -> str | None:
    """422 reason, or ``None`` when ``answer`` (already whitespace-normalized
    via ``normalize_answer``) is acceptable to grade. ``item`` is accepted
    for signature uniformity with satzbau's ``validate`` — unused here, same
    as the route today (zeitfaerbung/routes.py:162-169)."""
    if not answer:
        return "Type your answer first."
    if len(answer) > _MAX_ANSWER_CHARS:
        return "Keep it to one verb form — that looks like a paragraph."
    return None


async def grade(item: dict, answer: str, *, give_up: bool = False) -> tuple[dict, str]:
    """Returns ``(verdict, typed_for_ledger)``. ``verdict`` is EXACTLY the
    dict ``POST /zeitfaerbung/attempts`` returns (zeitfaerbung/routes.py:
    327-337) — ``accepted`` is ``[]`` when ``kind == "unrecognized"`` (the
    anti-devtools rule: an unrecognized answer must never leak the accepted
    forms). ``typed_for_ledger`` is NOT part of the HTTP response; it is the
    exact string the route's ledger write uses to reconstruct the learner's
    sentence (a give-up substitutes the literal ``"___"`` sentinel, an
    unrecognized/ambiguous answer keeps the raw typed text, and a resolved
    attempt keeps only the one recognized token) — kept out of the dict so
    callers that only want the HTTP-shaped verdict (the teacher route) can
    ignore it.
    """
    accepted = item["answers"]
    is_doppel = item["group"] in DOPPELDEUTIG_GROUPS
    reading = None
    alt = None

    if give_up:
        # FLOW-002 escape hatch: no recognizer runs — grade this exactly
        # like the "wrong verb entirely" branch below (same kind, same note
        # shape), so the frontend's existing wrong-verdict card renders with
        # no new branch. typed_for_ledger stays the literal gap marker — a
        # real attempt always substitutes a word here, so an unfilled "___"
        # in the ledger's sentence is itself the give-up sentinel (DATA-004
        # review can tell the two apart).
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
            # remains — but only when that leaves exactly one candidate (a
            # bare "war" typed alone never enters this branch).
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

    verdict = {
        "correct": correct,
        "kind": kind,
        "expected": expected,
        # "unrecognized" keeps the item live for a retry — shipping the
        # accepted forms there would answer it in devtools.
        "accepted": accepted if kind != "unrecognized" else [],
        "note": note,
        "reading": reading,
        "alt": alt,
        # GRAM-009: lets the frontend fetch GET /grammar/pattern/{id} for
        # the collapsed "Warum?" disclosure. Safe to ship even on
        # "unrecognized" — it names the broad taxonomy pattern, not the
        # specific accepted form, so it doesn't answer the item. camelCase
        # like every other practice payload in this repo.
        "patternId": item["pattern_id"],
    }
    return verdict, typed_for_ledger
