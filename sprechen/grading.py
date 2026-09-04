"""Pure post-transcription grading for Sprechen & transkribieren (CLARA-14),
extracted from ``sprechen/routes.py``'s two verdict-assembly sites so the
drill's own routes and ``teacher/registry.py``'s adapter (Clara's room)
share exactly one implementation.

Unlike the other five CLARA-13 drills, there is no separate ``validate()``
here: sprechen's only pre-grading validation is on the AUDIO itself (empty
clip, size cap) — I/O-adjacent work the route does before transcription even
runs, not a pure function over ``(item, answer)`` worth sharing. Transcription
itself (``satz/examiner.py::transcribe_attempt``) also stays out of this
module on purpose — it's I/O with its own Langfuse span and per-attempt STT
cost stamping (``agents/audio_costs.py``), not a grading step.

The drill's own routes stay the only place that touch the DB (ledger write,
DATA-004 log) and the coin gate — this module is pure aside from the judge
call itself.
"""

from sprechen.judge import judge_spoken


async def grade(item: dict, transcript: str, *, give_up: bool = False) -> tuple[dict, bool]:
    """Returns ``(verdict, judge_skipped)`` — the same two-element shape as
    the other five drills' ``grading.grade`` (CLARA-13's D1), even though
    sprechen has no deterministic short-circuit for a REAL attempt: every
    non-give-up attempt calls the judge, same as faelle/verbindungen/bauteil
    when their own deterministic match misses. ``judge_skipped`` is True only
    for a give-up — the one case that skips ``judge_spoken`` entirely, which
    is the same thing the flag means for the other four drills (the judge
    never ran). Neither sprechen route currently stamps a ``judge_skipped``
    span attribute (unlike faelle's), so this value is discarded at both call
    sites today — kept in the signature for shape-parity with the sibling
    ``grading`` modules and in case a caller wants it later.

    ``verdict`` is EXACTLY the dict ``POST /sprechen/attempts`` (a real
    attempt, sprechen/routes.py:303-316) and ``POST /sprechen/give-up``
    (sprechen/routes.py:389-398) each returned before this extraction — the
    two response shapes differ only by the give-up one carrying `"gaveUp":
    True` and skipping the judge, which is why they're folded into one
    function keyed on the `give_up` flag rather than kept as two.
    """
    if give_up:
        # FLOW-002 escape hatch, mirrored from POST /sprechen/give-up: no
        # audio was recorded, so there is nothing to transcribe or judge —
        # grade as a miss and reveal nothing more specific than that.
        return (
            {
                "transcript": "",
                "passed": False,
                "constraintMet": False,
                "constraintNote": "You gave up — no recording was judged.",
                "hits": 0,
                "hitQuotes": [],
                "slips": [],
                "gaveUp": True,
                # GRAM-009: lets the frontend fetch GET /grammar/pattern/{id}
                # for the collapsed "Warum?" disclosure. camelCase to match
                # this drill's own verdict convention (constraintMet, …).
                "patternId": item["pattern_id"],
            },
            True,  # judge_skipped
        )

    verdict = await judge_spoken(item, transcript)
    passed = verdict.constraint_met and not verdict.slips
    return (
        {
            "transcript": transcript,
            "passed": passed,
            "constraintMet": verdict.constraint_met,
            "constraintNote": verdict.constraint_note,
            "hits": verdict.hits,
            # Display-only, symmetric to slips (SPRECH-001) — defensive
            # getattr/or-empty so a judge response missing the field can't 500.
            "hitQuotes": getattr(verdict, "hit_quotes", None) or [],
            "slips": [
                {"quote": s.quote, "corrected": s.corrected, "note": s.note}
                for s in verdict.slips
            ],
            # GRAM-009: same as the give-up branch above.
            "patternId": item["pattern_id"],
        },
        False,  # judge_skipped
    )
