"""TEST-001 Proposal-1: seeded fixture profiles with shaped histories.

Imported by ``scripts/test_user.py`` (``create test-<id> --profile <name>``).
Five deterministic profiles, each a ``random.Random(f"spralingua-fixture:
{name}")``-seeded shape — same profile name always produces the same shape,
independent of the user id it's applied to, so a test run is reproducible.

    beginner   — A1, 0 ledger rows, 1 pack (a1-basics), 0 attempts, 0 streak,
                 signup-grant coins only. The "nothing has happened yet" case:
                 every ledger-dependent read (load_grammar_focus, the coach,
                 the round weighting) must return empty, not crash.
    plateaued  — A2, 3 HOT open patterns (recent + repeated + high
                 occurrences) + 2 cool open ones (old, low occurrences), ~60
                 drill_attempts over the last 3 weeks (hot patterns ~30%
                 correct, i.e. fail ~70%), 2 packs, a 4-day streak.
    retired    — B1, 14 patterns retired via the real credit_pattern_success
                 path (create open, then credit twice past _RETIRE_STREAK),
                 2 patterns left open, ~200 attempts ~90% correct, 3 packs.
    streaker   — B1, a 15-day streak (daily_mode_completions rows + the
                 users streak columns), a moderate ledger (4 open + 1
                 retired).
    polluted   — B1, a deliberately contradictory ledger: two patterns that
                 were retired AND reopened within the same week (churn), one
                 pattern whose stored example is contaminated (wrong
                 language — a judge misfire), one pattern whose stored
                 example looks like a Deepgram STT artifact (a trimmed
                 utterance) rather than a genuine learner slip, one plain
                 open pattern, one retired pattern that a handful of
                 drill_attempts rows then contradict (rows for that pattern
                 recorded as WRONG after the ledger already retired it).
                 Exists to prove the coach/debrief survive contradictory
                 state, not to be internally consistent.

Every profile grants the same signup coins a real account gets at row
creation (SIGNUP_GRANT into purchased_coins, via coins.engine.credit — the
real writer, not a raw UPDATE) so "coins" is never a blank column in the
verification output.

Repository-invariant workarounds (both concern BACKDATING — every writer in
database/repository.py and coins/engine.py that touches a timestamp hardcodes
"now" with no override, because real usage only ever happens at "now"; a
shaped fixture history needs attempts spread over weeks and a streak that
started days ago, which no writer alone can produce):

1. Ledger rows (user_errors) and drill_attempts. Where the writer's value is
   real invariant logic — record_grammar_error's occurrences/ring-buffer/
   reopen handling, credit_pattern_success's retire-streak threshold,
   record_drill_attempt's guard — this module calls the REAL writer for that
   logic (possibly several times, to build up occurrences/streak), then
   patches only the timestamp columns directly afterward under the same
   _assert_test_user guard: user_errors.first_seen/last_seen and each
   ring-buffer example's own "at" field (see _backdate_ledger_row).
   record_drill_attempt itself has no created_at parameter and, per its own
   docstring, has no invariant beyond "append one row, guard first, no merge,
   no lock" — so backdated attempts are inserted directly with an explicit
   created_at (_add_attempts), same guard, same columns, just skipping the
   now()-only writer rather than insert-then-patch.

2. Streak bookkeeping (credit_streak_day) can only ever credit "today" (the
   real wall-clock UTC date) — there is no way to replay N real
   credit_streak_day calls on N different past days in one script run. The
   first N-1 days of daily_mode_completions rows are inserted directly
   (complete_daily_mode always dates "today" too), and the users streak
   columns are pre-set to what N-1 consecutive credit_streak_day calls would
   have left behind (current_streak=N-1, last_streak_day=yesterday) — then
   the REAL credit_streak_day() runs once for real, applying the Nth
   (today's) day through the actual code path (gap math, longest_streak
   max(), the grace window untouched). See _seed_streak.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from coins.engine import credit as credit_coins
from coins.prices import SIGNUP_GRANT
from database.orm import (
    DailyModeCompletion,
    DrillAttempt,
    PackCard,
    User,
    UserCard,
    UserError,
)
from database.repository import (
    STREAK_MODES,
    _assert_test_user,
    credit_pattern_success,
    credit_streak_day,
    record_grammar_error,
)
from grammar.loader import load_taxonomy


# ── Small time helpers ──────────────────────────────────────────────────
# user_errors.first_seen/last_seen are plain (naive) TIMESTAMP columns,
# written via naive datetime.now() in record_grammar_error/
# credit_pattern_success — match that here. drill_attempts.created_at is
# TIMESTAMPTZ, compared against tz-aware UTC cutoffs elsewhere (stats/
# routes.py) — match that instead.


def _days_ago(n: float) -> datetime:
    return datetime.now() - timedelta(days=n)


def _days_ago_utc(n: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=n)


# ── Shared primitives, built on the real repository writers ────────────


async def _add_pack(db, user_id: str, pack_id: str) -> int:
    """Same INSERT…SELECT ON CONFLICT DO NOTHING as satz/routes.py::add_pack
    (SATZ-002) — reused verbatim rather than re-derived, so a fixture pool
    add obeys the exact same idempotency the real endpoint does."""
    _assert_test_user(user_id)
    result = await db.execute(
        pg_insert(UserCard)
        .from_select(
            ["user_id", "card_id", "source_pack"],
            select(literal(user_id), PackCard.card_id, literal(pack_id)).where(
                PackCard.pack_id == pack_id
            ),
        )
        .on_conflict_do_nothing(index_elements=["user_id", "card_id"])
    )
    await db.commit()
    return result.rowcount


async def _backdate_ledger_row(
    db, user_id: str, pattern_id: str, *, first_seen: datetime, last_seen: datetime
) -> None:
    """Patch a user_errors row's timestamps (and its ring-buffer examples'
    own "at" field, spread evenly between first_seen/last_seen) after the
    real writer(s) have already built its occurrences/status/examples."""
    row = await db.get(UserError, (user_id, pattern_id), with_for_update=True)
    if row is None:
        return
    row.first_seen = first_seen
    row.last_seen = last_seen
    if row.examples:
        n = len(row.examples)
        span = (last_seen - first_seen) / (n - 1) if n > 1 else timedelta(0)
        examples = []
        for i, ex in enumerate(row.examples):
            ex = dict(ex)
            ex["at"] = (first_seen + span * i).isoformat(timespec="seconds")
            examples.append(ex)
        row.examples = examples
    await db.commit()


async def _seed_open_pattern(
    db,
    user_id: str,
    pattern_id: str,
    catalog: dict,
    *,
    occurrences: int,
    first_seen: datetime,
    last_seen: datetime,
    source: str,
    sentence: str | None = None,
    corrected: str | None = None,
    note: str | None = None,
) -> None:
    """Build an OPEN ledger row from ``occurrences`` real record_grammar_error
    calls (occurrences count / reopen / ring-buffer cap all come from the
    real writer), then backdate. Defaults sentence/corrected/note to the
    taxonomy's own minimal-contrast pair — override to inject a bogus or
    ASR-artifact-looking example (the polluted profile)."""
    pattern = catalog[pattern_id]
    sentence = sentence if sentence is not None else pattern["wrong"]
    corrected = corrected if corrected is not None else pattern["right"]
    note = note if note is not None else pattern["description"]
    for _ in range(occurrences):
        await record_grammar_error(
            db,
            user_id=user_id,
            pattern_id=pattern_id,
            sentence=sentence,
            corrected=corrected,
            note=note,
            source=source,
        )
    await _backdate_ledger_row(db, user_id, pattern_id, first_seen=first_seen, last_seen=last_seen)


async def _retire_pattern(
    db,
    user_id: str,
    pattern_id: str,
    catalog: dict,
    *,
    first_seen: datetime,
    last_seen: datetime,
    source: str = "tandem",
) -> None:
    """Create then retire a pattern via the SAME credit path real greens use
    (create open via record_grammar_error, cross _RETIRE_STREAK=2 via two
    credit_pattern_success calls) — this is the exact path TEST-001's own
    incident was about, so the retired profile must not fake it any other
    way. Backdates afterward."""
    pattern = catalog[pattern_id]
    await record_grammar_error(
        db,
        user_id=user_id,
        pattern_id=pattern_id,
        sentence=pattern["wrong"],
        corrected=pattern["right"],
        note=pattern["description"],
        source=source,
    )
    await credit_pattern_success(db, user_id=user_id, pattern_id=pattern_id, source=source)
    await credit_pattern_success(db, user_id=user_id, pattern_id=pattern_id, source=source)
    await _backdate_ledger_row(db, user_id, pattern_id, first_seen=first_seen, last_seen=last_seen)


async def _add_attempts(db, user_id: str, attempts: list[dict]) -> int:
    """Direct drill_attempts inserts with an explicit (backdated) created_at.

    record_drill_attempt has no created_at parameter (always server_default
    now()) and, per its own docstring, carries no invariant beyond "append
    one row, guard first, no merge, no lock" — a direct insert under the
    same guard preserves that invariant exactly while allowing the
    historical spread real usage never needs.
    """
    _assert_test_user(user_id)
    for a in attempts:
        db.add(DrillAttempt(user_id=user_id, **a))
    await db.commit()
    return len(attempts)


async def _seed_streak(db, user_id: str, days: int, rng: random.Random) -> None:
    """N consecutive days ending today, each with 3 of the 4 STREAK_MODES
    completed (direct DailyModeCompletion inserts — complete_daily_mode
    always dates "today", see module docstring workaround #2), then N-1
    days of streak bookkeeping is faked directly and the REAL
    credit_streak_day() applies the Nth (today's) day for real."""
    today = datetime.now(timezone.utc).date()
    for i in range(days):
        day = today - timedelta(days=i)
        for mode in rng.sample(STREAK_MODES, k=3):
            await db.execute(
                pg_insert(DailyModeCompletion)
                .values(user_id=user_id, day=day, mode=mode)
                .on_conflict_do_nothing(index_elements=["user_id", "day", "mode"])
            )
    await db.commit()
    if days <= 1:
        # Let credit_streak_day do the only day itself — no backdating needed.
        await credit_streak_day(db, user_id=user_id)
        return
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            current_streak=days - 1,
            longest_streak=max(days - 1, 0),
            last_streak_day=today - timedelta(days=1),
        )
    )
    await db.commit()
    await credit_streak_day(db, user_id=user_id)  # real writer applies the final day


async def _grant_signup_coins(db, user_id: str) -> None:
    # coins.engine.credit() only flushes — callers own the commit (see its
    # one production call site, payments/webhook.py's topup handler, which
    # commits right after a successful credit). Mirror that here.
    await credit_coins(db, user_id=user_id, amount=SIGNUP_GRANT, kind="signup_grant", ref=user_id)
    await db.commit()


# ── Profiles ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProfileSpec:
    level: str
    summary: str


PROFILES: dict[str, ProfileSpec] = {
    "beginner": ProfileSpec(
        "A1",
        "0 ledger rows, 1 pack (a1-basics), 0 attempts, 0 streak, signup-grant coins only.",
    ),
    "plateaued": ProfileSpec(
        "A2",
        "3 hot open patterns (recent+repeated) + 2 cool open ones, ~60 attempts/3wk "
        "(hot ~30% correct), 2 packs, 4-day streak.",
    ),
    "retired": ProfileSpec(
        "B1",
        "14 patterns retired via credit_pattern_success, 2 open, ~200 attempts "
        "(~90% correct), 3 packs.",
    ),
    "streaker": ProfileSpec(
        "B1",
        "15-day streak (daily_mode_completions + streak columns), moderate ledger "
        "(4 open + 1 retired).",
    ),
    "polluted": ProfileSpec(
        "B1",
        "Contradictory ledger: 2 churned (retired-then-reopened same week), 1 bogus "
        "example, 1 ASR-artifact example, 1 plain open, 1 retired-but-contradicted "
        "by later failing attempts.",
    ),
}


async def _seed_beginner(db, user_id: str, rng: random.Random) -> dict:
    packs = await _add_pack(db, user_id, "a1-basics")
    await _grant_signup_coins(db, user_id)
    return {
        "ledger_open": 0, "ledger_retired": 0, "attempts": 0,
        "packs": ["a1-basics"], "cards_added": packs, "streak_days": 0,
    }


async def _seed_plateaued(db, user_id: str, rng: random.Random) -> dict:
    catalog = load_taxonomy()
    a2 = [p for p, v in catalog.items() if v["level"] == "A2"]
    a1 = [p for p, v in catalog.items() if v["level"] == "A1"]
    hot = rng.sample(a2, k=3)
    cool = rng.sample(a1, k=2)

    for pid in hot:
        await _seed_open_pattern(
            db, user_id, pid, catalog,
            occurrences=rng.randint(6, 9),
            first_seen=_days_ago(18),
            last_seen=_days_ago(rng.uniform(0.5, 2)),
            source="satz",
        )
    for pid in cool:
        await _seed_open_pattern(
            db, user_id, pid, catalog,
            occurrences=rng.randint(1, 2),
            first_seen=_days_ago(30),
            last_seen=_days_ago(rng.uniform(20, 26)),
            source="satz",
        )

    attempts = []
    for i in range(60):
        hot_turn = rng.random() < 0.6
        if hot_turn:
            pid = rng.choice(hot)
            correct = rng.random() < 0.30  # hot patterns fail ~70%
        else:
            pid = rng.choice(cool)
            correct = rng.random() < 0.80
        attempts.append(dict(
            exercise="satz", item_ref=f"n-{pid}-{i}", pattern_id=pid,
            correct=correct, word_ok=correct or rng.random() < 0.4,
            modality="spoken", session_id=f"satz-fixture-{i}",
            created_at=_days_ago_utc(rng.uniform(0, 21)),
        ))
    await _add_attempts(db, user_id, attempts)

    p1 = await _add_pack(db, user_id, "a1-basics")
    p2 = await _add_pack(db, user_id, "at-work")
    await _seed_streak(db, user_id, days=4, rng=rng)
    await _grant_signup_coins(db, user_id)

    return {
        "ledger_open": len(hot) + len(cool), "ledger_retired": 0,
        "hot_patterns": hot, "cool_patterns": cool,
        "attempts": len(attempts), "packs": ["a1-basics", "at-work"],
        "cards_added": p1 + p2, "streak_days": 4,
    }


async def _seed_retired(db, user_id: str, rng: random.Random) -> dict:
    catalog = load_taxonomy()
    retire_pool = [p for p, v in catalog.items() if v["level"] in ("A1", "A2")]
    open_pool = [p for p, v in catalog.items() if v["level"] == "B1"]
    retired_ids = rng.sample(retire_pool, k=min(14, len(retire_pool)))
    open_ids = rng.sample(open_pool, k=2)

    for i, pid in enumerate(retired_ids):
        first = _days_ago(70 - i * 3)
        last = first + timedelta(days=rng.uniform(3, 10))
        await _retire_pattern(
            db, user_id, pid, catalog, first_seen=first, last_seen=last,
            source=rng.choice(["satz", "faelle", "tandem"]),
        )
    for pid in open_ids:
        await _seed_open_pattern(
            db, user_id, pid, catalog,
            occurrences=rng.randint(2, 4),
            first_seen=_days_ago(15), last_seen=_days_ago(rng.uniform(1, 5)),
            source="tandem",
        )

    all_ids = list(catalog.keys())
    attempts = []
    for i in range(200):
        pid = rng.choice(all_ids)
        correct = rng.random() < 0.90
        attempts.append(dict(
            exercise=rng.choice(["satz", "verbformen", "faelle", "satzbau"]),
            item_ref=f"item-{i}", pattern_id=pid, correct=correct,
            word_ok=correct or rng.random() < 0.3,
            modality=rng.choice(["spoken", "written"]),
            session_id=f"fixture-{i}",
            created_at=_days_ago_utc(rng.uniform(0, 90)),
        ))
    await _add_attempts(db, user_id, attempts)

    packs = ["a1-basics", "b1-everyday", "at-work"]
    added = 0
    for pack_id in packs:
        added += await _add_pack(db, user_id, pack_id)
    await _grant_signup_coins(db, user_id)

    return {
        "ledger_open": len(open_ids), "ledger_retired": len(retired_ids),
        "open_patterns": open_ids, "retired_patterns": retired_ids,
        "attempts": len(attempts), "packs": packs, "cards_added": added,
        "streak_days": 0,
    }


async def _seed_streaker(db, user_id: str, rng: random.Random) -> dict:
    catalog = load_taxonomy()
    b1 = [p for p, v in catalog.items() if v["level"] == "B1"]
    a2 = [p for p, v in catalog.items() if v["level"] == "A2"]
    open_ids = rng.sample(b1, k=2) + rng.sample(a2, k=2)
    retired_candidates = [p for p in b1 if p not in open_ids]
    retired_id = rng.choice(retired_candidates)

    for pid in open_ids:
        await _seed_open_pattern(
            db, user_id, pid, catalog,
            occurrences=rng.randint(2, 4),
            first_seen=_days_ago(rng.uniform(10, 15)),
            last_seen=_days_ago(rng.uniform(1, 6)),
            source="tandem",
        )
    await _retire_pattern(
        db, user_id, retired_id, catalog,
        first_seen=_days_ago(20), last_seen=_days_ago(12), source="tandem",
    )

    attempts = []
    for i in range(25):
        pid = rng.choice(open_ids)
        correct = rng.random() < 0.6
        attempts.append(dict(
            exercise="satz", item_ref=f"n-{pid}-{i}", pattern_id=pid,
            correct=correct, word_ok=True, modality="spoken",
            session_id=f"satz-fixture-{i}",
            created_at=_days_ago_utc(rng.uniform(0, 15)),
        ))
    await _add_attempts(db, user_id, attempts)

    await _seed_streak(db, user_id, days=15, rng=rng)
    await _grant_signup_coins(db, user_id)

    return {
        "ledger_open": len(open_ids), "ledger_retired": 1,
        "open_patterns": open_ids, "retired_patterns": [retired_id],
        "attempts": len(attempts), "packs": [], "cards_added": 0,
        "streak_days": 15,
    }


async def _seed_polluted(db, user_id: str, rng: random.Random) -> dict:
    catalog = load_taxonomy()
    b1 = [p for p, v in catalog.items() if v["level"] == "B1"]
    rng.shuffle(b1)
    flapping = b1[0:2]
    bogus_id = b1[2]
    asr_id = b1[3]
    plain_id = b1[4]
    contradicted_retired_id = b1[5]

    # 1) Churned within the same week: create -> retire (2x credit) -> a
    # fresh slip reopens it, all inside the last 7 days.
    for pid in flapping:
        pattern = catalog[pid]
        await record_grammar_error(
            db, user_id=user_id, pattern_id=pid, sentence=pattern["wrong"],
            corrected=pattern["right"], note=pattern["description"], source="tandem",
        )
        await credit_pattern_success(db, user_id=user_id, pattern_id=pid, source="tandem")
        await credit_pattern_success(db, user_id=user_id, pattern_id=pid, source="tandem")
        await record_grammar_error(
            db, user_id=user_id, pattern_id=pid, sentence=pattern["wrong"],
            corrected=pattern["right"], note=pattern["description"] + " (recurred)",
            source="tandem",
        )
        await _backdate_ledger_row(
            db, user_id, pid, first_seen=_days_ago(6), last_seen=_days_ago(1),
        )

    # 2) Bogus example: the stored slip is wrong-language content (a judge
    # misfire), not a genuine German error.
    await _seed_open_pattern(
        db, user_id, bogus_id, catalog,
        occurrences=1, first_seen=_days_ago(5), last_seen=_days_ago(5),
        source="satz",
        sentence="Le chat est noir sur la table.",
        corrected=catalog[bogus_id]["right"],
        note="judge misfire: non-German input classified into this pattern",
    )

    # 3) ASR-artifact-looking row: a trimmed/garbled utterance (Deepgram gap),
    # not necessarily the learner's own slip — CLAUDE.md's rule that a
    # trimmed ending is the recognizer's, not the learner's.
    await _seed_open_pattern(
        db, user_id, asr_id, catalog,
        occurrences=1, first_seen=_days_ago(9), last_seen=_days_ago(9),
        source="tandem",
        sentence="Der Mann, der das Auto—",
        corrected=catalog[asr_id]["right"],
        note="likely ASR artifact: utterance trimmed mid-word, kept verbatim for POLLUTED fixture",
    )

    # 4) One plain, uncontroversial open pattern.
    await _seed_open_pattern(
        db, user_id, plain_id, catalog,
        occurrences=2, first_seen=_days_ago(10), last_seen=_days_ago(3),
        source="satz",
    )

    # 5) Retired, then directly contradicted by later drill_attempts rows
    # recording the SAME pattern as wrong — the ledger and the attempt log
    # disagree, on purpose.
    await _retire_pattern(
        db, user_id, contradicted_retired_id, catalog,
        first_seen=_days_ago(25), last_seen=_days_ago(20), source="tandem",
    )

    attempts = []
    # Contradicting rows: correct=True against a just-reopened (open) pattern...
    for i in range(3):
        pid = rng.choice(flapping)
        attempts.append(dict(
            exercise="tandem", item_ref=f"contradicts-open-{i}", pattern_id=pid,
            correct=True, modality="spoken", session_id=f"tandem-fixture-{i}",
            created_at=_days_ago_utc(rng.uniform(0.2, 2)),
        ))
    # ...and correct=False against the pattern the ledger says is retired.
    for i in range(3):
        attempts.append(dict(
            exercise="tandem", item_ref=f"contradicts-retired-{i}",
            pattern_id=contradicted_retired_id, correct=False, modality="spoken",
            session_id=f"tandem-fixture-r{i}",
            created_at=_days_ago_utc(rng.uniform(1, 10)),
        ))
    # The remainder: ordinary, non-contradicting noise.
    all_open = [*flapping, bogus_id, asr_id, plain_id]
    for i in range(29):
        pid = rng.choice(all_open)
        attempts.append(dict(
            exercise=rng.choice(["satz", "tandem", "faelle"]),
            item_ref=f"noise-{i}", pattern_id=pid,
            correct=rng.random() < 0.5, modality=rng.choice(["spoken", "written"]),
            session_id=f"fixture-{i}",
            created_at=_days_ago_utc(rng.uniform(0, 21)),
        ))
    await _add_attempts(db, user_id, attempts)
    await _grant_signup_coins(db, user_id)

    return {
        "ledger_open": 5, "ledger_retired": 1,
        "flapping_patterns": flapping, "bogus_pattern": bogus_id,
        "asr_pattern": asr_id, "plain_pattern": plain_id,
        "contradicted_retired_pattern": contradicted_retired_id,
        "attempts": len(attempts), "packs": [], "cards_added": 0,
        "streak_days": 0,
    }


_SEED_FUNCS = {
    "beginner": _seed_beginner,
    "plateaued": _seed_plateaued,
    "retired": _seed_retired,
    "streaker": _seed_streaker,
    "polluted": _seed_polluted,
}


async def seed_profile(db, user_id: str, profile_name: str) -> dict:
    """Apply ``profile_name``'s shaped history to an already-created
    ``user_id`` row. Deterministic: seeded off the profile name alone, not
    the user id, so re-running the same profile always builds the same shape.
    """
    if profile_name not in PROFILES:
        raise SystemExit(
            f"Unknown profile {profile_name!r}. Choices: {', '.join(sorted(PROFILES))}"
        )
    rng = random.Random(f"spralingua-fixture:{profile_name}")
    return await _SEED_FUNCS[profile_name](db, user_id, rng)
