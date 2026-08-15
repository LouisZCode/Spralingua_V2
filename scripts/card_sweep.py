"""Periodic MEASUREMENT sweep over LLM-forged Satzschmiede cards (SATZ-021,
Proposal-3).

Read-only. Never writes to the DB, never edits a card — a flagged row is a
lead for a human, not an instruction to auto-fix.

## Why this exists
The 2026-08-08 audit found 10.1% of ``community`` (LLM-forged) cards carried
a confirmed factual error (wrong sense, invented plural, wrong Perfekt
auxiliary, ...) against 0% of hand-written ``curated`` ones. ``satz/verifier.py``
(``verify_card``) now gates every new card at creation, but it was measured
against that same audit set at **~50% recall, 0% false alarms** (2 concurrent
votes, any rejection stands) — see ``_VERIFY_VOTES`` there. So the gate
should roughly halve the forge's error rate to ~5%, not zero it.

A later measurement (same day) tried closing that recall gap with a
three-model panel (Cerebras + two free OpenRouter models, unanimous-reject)
and it topped out at 57%, not the ~77% independent failures would predict —
because **the misses are correlated**: the six cards every model missed are
lookups (does this noun even have a plural?) and cross-field consistency
checks (does ``reflexive`` agree with the example's own "sich"?), not
sentences a model can judge better by re-reading them. No amount of
re-asking the same kind of question reaches that ceiling.

That is the whole argument for this script. It is not a smarter judge — it
reuses ``verify_card`` verbatim, so it inherits the same ~50% recall ceiling
per card. Its value is in running that check **periodically over cards that
already shipped**, so it (a) catches roughly the other half of the ~5%
residual the inline gate misses on any given card over repeated sampling,
and (b) is the ongoing meter on the gate itself: if the flagged rate stays
near the expected ~5%, the gate is holding; if it creeps up, something
upstream drifted (a prompt change, a model swap) and it's worth a fresh full
audit rather than trusting the inline gate blindly.

## Source values (checked 2026-08-15, local dev DB, read-only)
``cards.source`` holds exactly two values in this DB: ``community`` (LLM-
forged via the enricher, the ones this sweep cares about) and ``curated``
(hand-written YAML packs, synced at boot, audited at 0% error — not worth
periodically re-checking). Default ``--source community`` matches that.

## What gets rebuilt and verified
Only BASE cards (``tense IS NULL``) in the window are iterated — a verb's
past sibling (``tense='past'``) is not its own independent card; it shares
the base card's identity (gloss, article-less by the Verbs Rule) and was
fact-checked together with it as ONE ``EnrichedCard`` at forge time
(``satz/routes.py::_forge_card`` verifies the combined card; the DB split
into two rows is a storage detail). So for a verb this script looks up its
past sibling (if any, regardless of the sibling's own ``created_at``) and
folds ``tense_form``/``example`` back into ``past_form``/``past_example`` on
one rebuilt ``EnrichedCard`` — mirroring, in reverse, the mapping
``satz/routes.py::_forge_card``/``_ensure_past_sibling`` write forward. A
missing past sibling is a legitimate state (the gate may have given up on
it rather than ship a wrong one) and is left as ``None``, same as
``verify_card`` already tolerates.

Usage:
    uv run python scripts/card_sweep.py [--since 7d|30d|YYYY-MM-DD]
        [--source community|all] [--votes 2] [--limit N] [--out path.md]
"""

import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Repo-root packages (config, database, satz) aren't installed — every other
# entry point (uvicorn, alembic) is invoked FROM the repo root so plain
# imports resolve. `python scripts/card_sweep.py` puts `scripts/`, not the
# repo root, on sys.path[0], so add the repo root explicitly before the
# repo-local imports below.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from config import database_url
from database.connection import dispose_engine, get_sessionmaker, init_engine
from database.orm import VocabCard
from satz.enricher import EnrichedCard
import satz.verifier as verifier_module
from satz.verifier import CardVerdict, verify_card

_RELATIVE_SINCE_RE = re.compile(r"^(\d+)d$")

# Matches the AUDIT-measured expectation from the 2026-08-08 gate update:
# the gate should take the forge's 10.1% down to "roughly 5%", not to zero.
# A sweep result comfortably at/under that is "the gate is holding"; above
# it is a signal to go re-run the full audit rather than trust this proxy.
_GATE_HEALTH_THRESHOLD_PCT = 5.0


# ---------------------------------------------------------------------------
# --since parsing
# ---------------------------------------------------------------------------

def parse_since(value: str) -> tuple[datetime, str]:
    """'7d' / '30d' -> N days back from now (UTC); 'YYYY-MM-DD' -> that date
    (UTC midnight). Returns (cutoff, human-readable label for the report).

    ``cards.created_at`` is ``timestamp without time zone`` written via
    Postgres ``now()`` in a UTC-configured session (checked 2026-08-15:
    ``SELECT now()`` returned a ``+00`` offset) — so naive UTC is the right
    comparison basis here, no tz-aware/naive mismatch at the SQL boundary.
    """
    m = _RELATIVE_SINCE_RE.match(value.strip())
    if m:
        days = int(m.group(1))
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        return cutoff, f"last {days} day{'s' if days != 1 else ''} (created_at >= {cutoff:%Y-%m-%d %H:%M} UTC)"
    try:
        cutoff = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        raise SystemExit(
            f"--since must be 'Nd' (e.g. 7d, 30d) or 'YYYY-MM-DD', got {value!r}"
        )
    return cutoff, f"created_at >= {cutoff:%Y-%m-%d} UTC"


# ---------------------------------------------------------------------------
# DB read (read-only — no writes anywhere in this module)
# ---------------------------------------------------------------------------

async def load_window(db, cutoff: datetime, source: str, limit: int | None):
    """Base cards (tense IS NULL) created since ``cutoff``, plus a bulk
    lookup of every verb's past sibling (any age) so verbs can be rebuilt as
    ONE combined card. Read-only: two SELECTs, nothing else."""
    stmt = select(VocabCard).where(
        VocabCard.tense.is_(None), VocabCard.created_at >= cutoff
    )
    if source != "all":
        stmt = stmt.where(VocabCard.source == source)
    stmt = stmt.order_by(VocabCard.created_at.desc())
    if limit:
        stmt = stmt.limit(limit)
    base_cards = list((await db.scalars(stmt)).all())

    verb_targets = {c.target.lower() for c in base_cards if c.type == "verb" and c.target}
    siblings_by_target: dict[str, VocabCard] = {}
    if verb_targets:
        sib_stmt = select(VocabCard).where(
            VocabCard.type == "verb",
            VocabCard.tense == "past",
            func.lower(VocabCard.target).in_(verb_targets),
        )
        for sib in (await db.scalars(sib_stmt)).all():
            siblings_by_target[sib.target.lower()] = sib

    return base_cards, siblings_by_target


def rebuild_enriched(card: VocabCard, sibling: VocabCard | None) -> EnrichedCard:
    """Reverse of the forward mapping in ``satz/routes.py``
    (``_forge_card``'s ``VocabCard(...)`` construction and
    ``_ensure_past_sibling``'s ``values = dict(...)``): DB row(s) -> the
    ``EnrichedCard`` shape ``verify_card`` expects. Raises (pydantic
    ``ValidationError`` or plain ``ValueError``) on anything that doesn't
    fit the card rules — the caller counts that as a skip rather than
    crashing the sweep over one bad row."""
    return EnrichedCard(
        valid=True,
        type=card.type,
        target=card.target,
        article=card.article,
        reflexive=bool(card.reflexive),
        gloss=card.gloss,
        note=card.note,
        example=card.example,
        past_form=sibling.tense_form if sibling else None,
        past_example=sibling.example if sibling else None,
        level=card.level,
    )


# ---------------------------------------------------------------------------
# Verification (reuses satz/verifier.py verbatim — see module docstring)
# ---------------------------------------------------------------------------

async def _verify_one(sem: asyncio.Semaphore, session_id: str, card: VocabCard, enriched: EnrichedCard):
    async with sem:
        try:
            verdict = await verify_card(enriched, user_id="card-sweep", session_id=session_id)
            return card, enriched, verdict, None
        except Exception as exc:  # noqa: BLE001 — recorded, not raised; one bad call must not kill the sweep
            return card, enriched, None, exc


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _failed_checks(v: CardVerdict) -> str:
    failed = [
        name
        for name, ok in (
            ("gloss_sense_ok", v.gloss_sense_ok),
            ("forms_ok", v.forms_ok),
            ("example_ok", v.example_ok),
        )
        if not ok
    ]
    return ", ".join(failed) if failed else "(none — verdicts disagreed)"


def _card_facts_lines(card: VocabCard, enriched: EnrichedCard) -> list[str]:
    lines = [f"- type: {card.type}", f"- target: {card.target}"]
    if enriched.article:
        lines.append(f"- article: {enriched.article}")
    if enriched.reflexive:
        lines.append("- reflexive: true")
    if enriched.gloss:
        lines.append(f"- gloss: {enriched.gloss}")
    if enriched.note:
        lines.append(f"- note: {enriched.note}")
    if enriched.example:
        lines.append(f"- example: {enriched.example}")
    if enriched.past_form:
        lines.append(f"- past_form: {enriched.past_form}")
    if enriched.past_example:
        lines.append(f"- past_example: {enriched.past_example}")
    lines.append(f"- source: {card.source}  ·  created_at: {card.created_at}  ·  first_added_by: {card.first_added_by}")
    return lines


def build_report(
    *,
    window_label: str,
    source: str,
    votes: int,
    total_in_window: int,
    skipped: list[tuple[VocabCard, str]],
    verify_errors: list[tuple[VocabCard, Exception]],
    results: list[tuple[VocabCard, EnrichedCard, CardVerdict]],
) -> str:
    flagged = [(c, e, v) for c, e, v in results if not v.ok]
    clean = [r for r in results if r[2].ok]
    verified_n = len(results)
    flag_rate = (len(flagged) / verified_n * 100) if verified_n else 0.0

    if verified_n == 0:
        health = "N/A — nothing was verified"
    elif flag_rate <= _GATE_HEALTH_THRESHOLD_PCT:
        health = f"gate holding ({flag_rate:.1f}% <= {_GATE_HEALTH_THRESHOLD_PCT:.0f}% expectation)"
    else:
        health = f"INVESTIGATE ({flag_rate:.1f}% > {_GATE_HEALTH_THRESHOLD_PCT:.0f}% expectation)"

    lines = [
        "# Card sweep report (SATZ-021 Proposal-3)",
        "",
        f"- **Window:** {window_label}",
        f"- **Source filter:** {source}",
        f"- **Votes per card:** {votes}",
        f"- **Run at:** {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---|",
        f"| cards in window | {total_in_window} |",
        f"| rebuilt & verified | {verified_n} |",
        f"| skipped (could not rebuild) | {len(skipped)} |",
        f"| verify_card call errors | {len(verify_errors)} |",
        f"| verified OK | {len(clean)} |",
        f"| flagged | {len(flagged)} |",
        f"| flag rate | {flag_rate:.1f}% |",
        "",
        f"**Gate health: {health}**",
        "",
    ]

    lines.append("## Flagged cards")
    lines.append("")
    if not flagged:
        lines.append("None.")
    else:
        lines.append("| id | type | target | failed checks | problem |")
        lines.append("|---|---|---|---|---|")
        for card, enriched, v in flagged:
            problem = (v.problem or "").replace("|", "/").replace("\n", " ")
            lines.append(
                f"| {card.id} | {card.type} | {card.target} | {_failed_checks(v)} | {problem} |"
            )
        lines.append("")
        lines.append("### Flagged card details")
        for card, enriched, v in flagged:
            lines.append("")
            lines.append(f"**{card.id}** — {_failed_checks(v)}")
            if v.example_meaning:
                lines.append(f"- example_meaning (verifier's translation): {v.example_meaning}")
            lines.extend(_card_facts_lines(card, enriched))

    if skipped:
        lines.append("")
        lines.append("## Skipped (could not rebuild an EnrichedCard)")
        lines.append("")
        lines.append("| id | type | target | reason |")
        lines.append("|---|---|---|---|")
        for card, reason in skipped:
            lines.append(f"| {card.id} | {card.type} | {card.target} | {reason} |")

    if verify_errors:
        lines.append("")
        lines.append("## verify_card call errors (excluded from flag rate)")
        lines.append("")
        lines.append("| id | type | target | error |")
        lines.append("|---|---|---|---|")
        for card, exc in verify_errors:
            lines.append(f"| {card.id} | {card.type} | {card.target} | {exc!r} |")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="SATZ-021 Proposal-3: periodic measurement sweep over forged Satzschmiede cards. Read-only."
    )
    parser.add_argument("--since", default="7d", help="'7d', '30d', or 'YYYY-MM-DD' (default: 7d)")
    parser.add_argument("--source", choices=["community", "all"], default="community")
    parser.add_argument("--votes", type=int, default=2, help="verify_card votes per card (default: 2, matches the inline gate)")
    parser.add_argument("--limit", type=int, default=None, help="cap the number of cards processed (cost bound)")
    parser.add_argument("--out", type=Path, default=None, help="also write the markdown report here")
    args = parser.parse_args()

    cutoff, window_label = parse_since(args.since)

    # verify_card reads `_VERIFY_VOTES` from module globals at CALL time, not
    # at def time (plain name lookup in the function body) — so overriding
    # the module attribute once, before any concurrent calls start, is safe
    # and needs no monkeypatch of the function itself. See satz/verifier.py.
    if args.votes != verifier_module._VERIFY_VOTES:
        verifier_module._VERIFY_VOTES = args.votes

    await init_engine(database_url)
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as db:
            base_cards, siblings_by_target = await load_window(db, cutoff, args.source, args.limit)
    finally:
        await dispose_engine()

    skipped: list[tuple[VocabCard, str]] = []
    to_verify: list[tuple[VocabCard, EnrichedCard]] = []
    for card in base_cards:
        sibling = siblings_by_target.get((card.target or "").lower()) if card.type == "verb" else None
        try:
            enriched = rebuild_enriched(card, sibling)
        except Exception as exc:  # noqa: BLE001 — one bad row must not kill the sweep
            skipped.append((card, repr(exc)))
            continue
        to_verify.append((card, enriched))

    # OBS-006/SATZ-021: distinct Langfuse session per sweep run so its
    # "satz-forge-verify" spans (same name as the live inline gate) group
    # together under a session id that is obviously NOT a real user's, and
    # user_id="card-sweep" tags every span the same way. Cheap — verify_card
    # already accepts both kwargs, this is not a new code path.
    session_id = f"card-sweep-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    sem = asyncio.Semaphore(4)  # Cerebras rate limits are real (see brief).
    outcomes = await asyncio.gather(
        *(_verify_one(sem, session_id, card, enriched) for card, enriched in to_verify)
    )

    results: list[tuple[VocabCard, EnrichedCard, CardVerdict]] = []
    verify_errors: list[tuple[VocabCard, Exception]] = []
    for card, enriched, verdict, err in outcomes:
        if err is not None:
            verify_errors.append((card, err))
        else:
            results.append((card, enriched, verdict))

    report = build_report(
        window_label=window_label,
        source=args.source,
        votes=args.votes,
        total_in_window=len(base_cards),
        skipped=skipped,
        verify_errors=verify_errors,
        results=results,
    )

    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"[written to {args.out}]", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
