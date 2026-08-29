"""Live single-gap exercise forge for Clara's dev-only ``[[ÜBUNG-NEU: ...]]``
marker (CLARA-15 P3).

Two structured-output LLM calls per topic — draft, then a blind-rederive
verify pass — build ONE fresh German gap-fill item on the spot, the same
draft-then-verify shape ``drills/forge.py`` uses for personal Bauteil/
Verbindungen items, but synchronous on the request path (a developer is
waiting on the reply) and scoped to a free-text topic string rather than a
vocab card. The graded shape is Verbindungen's NATIVE verdict —
``{correct, expected, chunk, note}`` — because ``teacher/routes.py`` serves a
forged item through the same ``VerbindungenTrainer`` component real
Verbindungen items use (see ``teacher/registry.py::_serve_verbindungen``).

The in-memory store below is a DELIBERATE, DOCUMENTED exception to the
"no module-level singletons" rule in CLAUDE.md: this is process-local,
dev-preview state by design — losing it on a restart only costs a re-deal,
never a learner-facing loss, since real learners can never reach this path
at all (the route 403s them, and Clara's prompt never offers the marker that
would ask for it — see ``agents/prompts/teacher.yaml``'s ``fallback_forge``
vs ``fallback_wunsch``).
"""

import time
from typing import Optional
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm

FORGE_MODEL = "openai/gpt-oss-120b"

# --------------------------------------------------------------------------
# In-memory store: item_id -> (item dict, monotonic expiry deadline).
# TTL 2h, cap 50 (oldest evicted first — with a constant TTL, "oldest
# inserted" and "soonest to expire" are the same ordering). Never persisted,
# never touches the DB.
# --------------------------------------------------------------------------

_TTL_S = 2 * 60 * 60  # 2 hours
_STORE_CAP = 50

_STORE: dict[str, tuple[dict, float]] = {}


def _prune(now: float) -> None:
    expired = [item_id for item_id, (_, deadline) in _STORE.items() if deadline <= now]
    for item_id in expired:
        _STORE.pop(item_id, None)


def store_item(item: dict) -> None:
    """Insert a freshly forged item, TTL 2h. Evicts the oldest entry first
    when the store is already at cap (50) — a dev-preview cache, not a
    guarantee that every forged item survives to be attempted."""
    now = time.monotonic()
    _prune(now)
    if len(_STORE) >= _STORE_CAP:
        oldest_id = min(_STORE, key=lambda k: _STORE[k][1])
        _STORE.pop(oldest_id, None)
    _STORE[item["id"]] = (item, now + _TTL_S)


def get_item(item_id: str) -> Optional[dict]:
    """Look up a stored item, or ``None`` when missing or expired (the
    caller — ``teacher/routes.py`` — turns that into a 404, same contract as
    every other drill's stale-item behavior)."""
    now = time.monotonic()
    entry = _STORE.get(item_id)
    if entry is None:
        return None
    item, deadline = entry
    if deadline <= now:
        _STORE.pop(item_id, None)
        return None
    return item


# --------------------------------------------------------------------------
# Draft + verify schemas. Cerebras strict json_schema limits apply (see
# CLAUDE.md): root object, additionalProperties:false (langchain's
# to_strict_json_schema adds this automatically for a plain pydantic model,
# same as every sibling judge schema in this repo — no manual model_config
# needed), no pattern/format/minItems/maxItems/minLength/maxLength. The
# "exactly one ___" / "at most 6 accepts" rules are therefore enforced in
# PYTHON below (_sane / list slicing), never in the schema itself.
# --------------------------------------------------------------------------


class ForgeDraft(BaseModel):
    """One drafted single-gap German item for a free-text topic."""

    sentence: str = Field(
        description=(
            "German sentence with exactly one ___ gap testing the topic. "
            "A1-B1 vocabulary unless the topic itself needs higher. "
            "Contemporary, natural German a real teacher would say"
        )
    )
    answer: str = Field(description="The words that correctly fill the gap")
    accepts: list[str] = Field(
        description="Every filler that is ALSO correct for this gap, including answer itself, at most 6 entries"
    )
    hint_en: str = Field(
        description="Natural full-sentence English rendering of the filled sentence"
    )
    rule_note: str = Field(
        description="One short English sentence naming the grammar rule the gap tests"
    )
    title: str = Field(description="Two to four words naming the exercise topic")


class ForgeVerify(BaseModel):
    """Blind second-pass check on a drafted item: derive the gap yourself
    before trusting the draft's claimed answer."""

    derived_answer: str = Field(
        description="The word(s) you independently work out belong in the gap, from the sentence's own grammar alone — before weighing the draft's claim"
    )
    ok: bool = Field(
        description="True iff the sentence has exactly one unambiguous gap and the draft's answer plus accepts all correctly fill it, matching your own derivation"
    )
    accepts: list[str] = Field(
        description="Corrected list of every filler that is genuinely correct for the gap, at most 6, always including derived_answer when ok is true"
    )
    reason: str = Field(description="One short line: why ok is false, or 'ok' when true")


class ForgeMissVerdict(BaseModel):
    """Verdict on a MISSED forged gap-fill answer — the deterministic accepts
    match already failed; grade whether the typed filler should have been
    accepted anyway. Isolates the gap alone, same convention as
    ``faelle/judge.py``."""

    correct: bool = Field(
        description="True when the typed filler is ALSO a grammatically and semantically correct filler for the gap, even though it wasn't in the pre-approved list"
    )
    note: Optional[str] = Field(
        default=None,
        description="REQUIRED when correct is false — at most 14 words naming what's wrong. Null when correct",
    )


DRAFT_PROMPT = """# Role
You draft ONE single-gap German exercise item for a topic a student asked their teacher about, live, on the spot.

# Topic
{topic}

# What to build
- `sentence` — ONE natural, contemporary German sentence with EXACTLY ONE `___` gap that tests the topic above. A1-B1 vocabulary throughout, unless the topic itself genuinely needs a higher level (e.g. a B2 tense) — never harder than the topic requires.
- `answer` — the words that correctly fill the gap.
- `accepts` — every filler that is ALSO correct here (synonyms, equally valid case/form choices), including `answer` itself; at most 6.
- `hint_en` — a natural, full-sentence English rendering of the filled sentence (comprehension only, not a translation drill).
- `rule_note` — ONE short English sentence naming the rule the gap tests.
- `title` — two to four words naming the exercise.

# Hard rules
- Exactly one `___` in `sentence` — never zero, never more than one.
- The sentence must be unambiguous GIVEN THE FRAME — a fluent speaker reading it must land on `answer` (or an `accepts` sibling), not on any other filler.
- Natural, everyday German a real teacher would actually use — never a stilted textbook fragment.
"""

VERIFY_PROMPT = """# Role
You are a strict second pass on a live-forged German gap-fill exercise, before it ever reaches a student. Work out the gap YOURSELF first — do not simply trust the draft.

# The item
- sentence with gap: "{sentence}"
- rule being tested: {rule_note}
- draft's claimed answer: "{answer}"
- draft's claimed accepts: {accepts}

# What to do
1. Read ONLY the sentence and the rule. Work out, from the sentence's own grammar, what word(s) belong in the gap — write that in `derived_answer` before weighing anything the draft claimed.
2. Compare: does the draft's `answer` match what you derived (or an equally correct sibling)? Is every entry in `accepts` ALSO genuinely correct for this gap? Is the sentence unambiguous, with exactly one gap?
3. Set `ok` to true only if all of that holds. Otherwise false, with `reason` naming the problem in one short line (else "ok").
4. `accepts` in your OWN answer is the corrected list — add anything genuinely correct the draft missed, remove anything not actually correct; always include `derived_answer` when `ok` is true.
"""

MISS_PROMPT = """# Role
You grade ONE missed answer to a live-forged German gap-fill exercise. The learner's answer did not match any pre-approved filler — decide honestly whether it should have been accepted anyway. Grade ONLY the gap; everything else in the sentence was given to the learner and is not in question.

# The item
- sentence: "{sentence}"
- one correct answer (a reference, not the only one): "{expected}"
- the rule being tested: {rule_note}

# What the learner typed
"{typed}"

# Worked examples
- expected "einen Kuchen" · rule "accusative direct object" · typed "einen Kuchen." (trailing period) → **correct: true**. Punctuation is not grammar.
- expected "gestern" · rule "time adverb, simple past narration" · typed "heute" → **correct: false**. Different word, wrong meaning — "heute" (today) does not fit where the sentence needs a past-time adverb. note: "heute means today, not yesterday — wrong tense fit".
- expected "der Fahrer" · rule "nominative subject" · typed "den Fahrer" → **correct: false**. Right noun, wrong case for the subject slot. note: "that's accusative — the subject here needs nominative der".
- expected "mit ihm" · rule "dative pronoun after mit" · typed "mit ihn" → **correct: false**. CONTROL: accusative pronoun after a dative preposition. note: "ihn is accusative — mit takes dative, so ihm".

# Grade
- `correct` — true only if the typed filler is grammatically correct AND fits the sentence's meaning as well as the reference answer.
- `note` — REQUIRED when correct=false, at most 14 words, naming exactly what's wrong. Never restate the correct answer (it's shown separately). Null when correct.
"""


async def _draft(topic: str, *, reason: Optional[str] = None) -> ForgeDraft:
    # JUDGE-001 (2026-08-15): drafts CONTENT, not a verdict — temperature=None
    # keeps provider-default sampling, same convention as drills/forge.py's
    # draft calls and satz/enricher.py.
    llm = structured_judge_llm(ForgeDraft, temperature=None)
    prompt = DRAFT_PROMPT.replace("{topic}", topic)
    if reason:
        prompt += (
            f"\n\n# Your last attempt was rejected\n{reason}\n"
            f"Fix exactly that; keep everything else that was fine.\n"
        )
    with generation_span("teacher-forge-draft", model=FORGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result


async def _verify(draft: ForgeDraft) -> ForgeVerify:
    # temperature=0 default (structured_judge_llm) — this IS a verdict.
    llm = structured_judge_llm(ForgeVerify)
    prompt = (
        VERIFY_PROMPT.replace("{sentence}", draft.sentence)
        .replace("{rule_note}", draft.rule_note)
        .replace("{answer}", draft.answer)
        .replace("{accepts}", ", ".join(draft.accepts))
    )
    with generation_span("teacher-forge-verify", model=FORGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result


def _sane(draft: ForgeDraft) -> Optional[str]:
    """Cheap structural gate BEFORE ever calling the verify LLM — a reason
    string when something's off (fed back into the redraft), else None."""
    if draft.sentence.count("___") != 1:
        return "the sentence must contain exactly one ___ gap"
    if not draft.answer.strip():
        return "answer must not be empty"
    if not any(a.strip() for a in draft.accepts):
        return "accepts must include at least the answer"
    return None


async def forge_item(topic: str) -> dict:
    """Draft -> verify a single-gap German item for ``topic``. One redraft on
    a rejected first pass (sanity gate OR verify), then raises.

    Returns an internal dict — id/topic/title/frame/hint/answer/accepts/
    rule_note — that ``teacher/routes.py`` both stores whole (via
    :func:`store_item`, for grading) and projects down to the served
    round-item shape (``{id, frame, hint}``, mirroring
    ``teacher/registry.py::_serve_verbindungen`` field-for-field).
    """
    reason: Optional[str] = None
    for attempt in (1, 2):
        draft = await _draft(topic, reason=reason)
        gate_reason = _sane(draft)
        if gate_reason is not None:
            logger.warning(f"[FORGE] draft #{attempt} for {topic!r} failed sanity gate: {gate_reason}")
            reason = gate_reason
            continue

        verify = await _verify(draft)
        if verify.ok:
            accepts = list(
                dict.fromkeys(
                    [*(a.strip() for a in (verify.accepts or []) if a and a.strip()),
                     verify.derived_answer.strip(),
                     draft.answer.strip()]
                )
            )
            if draft.answer.strip() not in accepts:
                accepts.insert(0, draft.answer.strip())
            accepts = accepts[:6]
            return {
                "id": f"forge-{uuid4().hex}",
                "topic": topic,
                "title": draft.title,
                "frame": draft.sentence,
                "hint": draft.hint_en,
                "answer": draft.answer,
                "accepts": accepts,
                "rule_note": draft.rule_note,
            }

        logger.warning(f"[FORGE] draft #{attempt} for {topic!r} rejected by verify: {verify.reason}")
        reason = verify.reason

    raise RuntimeError(f"forge_item: exhausted retries for topic={topic!r} last_reason={reason!r}")


# --------------------------------------------------------------------------
# Grading — mirrors verbindungen/grading.py's shape and normalization
# (case/whitespace-insensitive match against a pre-approved list, judge only
# on a miss), returning verbindungen's NATIVE verdict:
# {correct, expected, chunk, note}. `chunk` carries the gap answer (a
# forged item has no fixed-chunk concept the way real Verbindungen items
# do, but VerbindungenTrainer reads this field, so it must be present) and
# `expected` is the canonical answer — same value, per spec.
# --------------------------------------------------------------------------


def _normalize(s: str) -> str:
    return " ".join(s.split()).strip()


async def grade_forged(item: dict, answer: str, *, give_up: bool = False) -> tuple[dict, bool]:
    """Returns ``(verdict, judge_skipped)``. ``judge_skipped`` is True only
    when give-up or the deterministic accepts-match short-circuited the
    judge call — same contract as every sibling drill's ``grade``."""
    if give_up:
        return (
            {
                "correct": False,
                "expected": item["answer"],
                "chunk": item["answer"],
                "note": item["rule_note"],
            },
            True,
        )

    typed = _normalize(answer)
    accepts_norm = {_normalize(a).lower() for a in item["accepts"]}
    if typed.lower() in accepts_norm:
        return (
            {
                "correct": True,
                "expected": item["answer"],
                "chunk": item["answer"],
                "note": None,
            },
            True,
        )

    # Miss — ONE generic judge call (schema-factory, temp 0 default), a
    # worked-example prompt over the item's own rule_note. Judge failure
    # fails SOFT to the deterministic-miss verdict — never 502 an attempt
    # deterministic logic can already answer.
    try:
        verdict = await _judge_miss(item, typed)
        return (
            {
                "correct": verdict.correct,
                "expected": item["answer"],
                "chunk": item["answer"],
                "note": verdict.note,
            },
            False,
        )
    except Exception:
        logger.exception(f"[FORGE] judge call failed for item {item['id']}")
        return (
            {
                "correct": False,
                "expected": item["answer"],
                "chunk": item["answer"],
                "note": item["rule_note"],
            },
            False,
        )


async def _judge_miss(item: dict, typed: str) -> ForgeMissVerdict:
    # temperature=0 default (structured_judge_llm) — this IS a verdict.
    llm = structured_judge_llm(ForgeMissVerdict)
    prompt = (
        MISS_PROMPT.replace("{sentence}", item["frame"])
        .replace("{expected}", item["answer"])
        .replace("{rule_note}", item["rule_note"])
        .replace("{typed}", typed)
    )
    with generation_span("teacher-forge-judge", model=FORGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    if result.correct:
        result.note = None
    return result
