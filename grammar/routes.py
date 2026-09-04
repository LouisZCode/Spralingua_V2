"""HTTP route for the curated grammar-pattern explanation bank (GRAM-009).

One read-only endpoint, ``GET /grammar/pattern/{pattern_id}``, surfacing the
vetted content ``grammar/explanations.yaml`` already carries for all 33
taxonomy patterns. Until now the bank's only consumers were both inside
Clara's voice room (``agents/conversational_prompt.py``'s kickoff seed and
``agents/pipecat_wrapper.py``'s ``_augment_first_result``) — invisible to a
free learner (Clara is ``limit: 0`` on free) and, even for a paying one,
governing only her opening turn. This route reaches every typed drill's
wrong-answer card and the ``/development`` focus cards instead.

Free (no coin gate) — same bucket as idiom/gloss/nudge/explain in
``coins/prices.py``'s docstring: this is read-only pedagogy content, not a
priced action. Still requires the session JWT like every other route
(``auth.deps.get_current_user_id``) — no anonymous scraping of the bank.

Deliberately omits ``native_note`` (and ``source``) from the response.
``native_note`` exists specifically to dodge the "gpt-oss-120b drops
instructions about a future turn" trap (``conversational_prompt.py:501-503``,
``pipecat_wrapper.py``'s ``_augment_first_result``) and must stay Clara's own
casual-aside mechanism, delivered as a stage direction on her session's
first result turn — not something that rides along by accident because this
route returns the whole bank entry. ``source`` is an internal attribution
field (a german.stackexchange question id / null) with no learner-facing
purpose.
"""

from fastapi import APIRouter, Depends, HTTPException

from auth.deps import get_current_user_id
from grammar.loader import load_explanations

router = APIRouter(prefix="/grammar", tags=["grammar"])


@router.get("/pattern/{pattern_id}")
async def get_pattern_explanation(
    pattern_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Return the curated ``pair``/``point``/``test`` for one taxonomy
    pattern id — the same fields Clara's kickoff draws on, minus
    ``native_note`` (see module docstring).

    404s for any id absent from the bank. Today that only happens for an id
    that isn't a real ``grammar/taxonomy.yaml`` pattern at all — the bank
    currently covers all 33 taxonomy ids (``load_explanations()`` raises at
    load time if any taxonomy pattern were left uncovered) — but the check
    stays a plain dict lookup rather than assuming that coverage holds
    forever.
    """
    bank = load_explanations()
    entry = bank.get(pattern_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No explanation for this pattern.")
    return {
        "pattern_id": pattern_id,
        "pair": entry["pair"],
        "point": entry["point"],
        "test": entry["test"],
    }
