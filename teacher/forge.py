"""Live production-exercise forge for Clara's dev-only ``[[ÜBUNG-NEU: ...]]``
marker (CLARA-15 P3, rewritten for CLARA-16 — "the produce format").

Two structured-output LLM calls per topic — draft, then a blind-rederive
verify pass — build ONE fresh German PRODUCTION task on the spot, the same
draft-then-verify shape ``drills/forge.py`` uses for personal Bauteil/
Verbindungen items, but synchronous on the request path (a developer is
waiting on the reply) and scoped to a free-text topic string rather than a
vocab card.

v1 (single-gap fill-the-blank, ``ForgeDraft``/``ForgeVerify``/
``ForgeMissVerdict`` — deleted this round) was pedagogically shallow: the
owner's own words testing it were "I just had to write the word HATT — no
learning." v2 replaces the format entirely. The forge now builds a
production task — an English instruction like "Make a wish about your
weekend using 'hätte'" — and the learner answers with ONE complete German
sentence of their own, typed OR spoken (audio → Deepgram STT → the same
judge as the typed path). Constraint-judged free production has NO
accept-list at all: the judge grades the learner's own sentence against the
task and the target structure directly, which also removes v1's worst
failure mode — a wrong, pre-approved accepts list failing a learner who was
right.

This module is still dev-only (``teacher/routes.py`` 403s any non-developer
before ever calling in here) and still writes nothing — see the ABSOLUTE
INVARIANT blocks on every route in ``teacher/routes.py``. Clara's room is
deliberately exempt from every evaluator, and a practice item handed out
inside that room must never become a side-channel into the learning-state
tables the exemption exists to keep her out of.

CLARA-17 ("the exercise factory, round 1") adds two more ways to build an
item, both reused whole by ``teacher/dealer.py`` — the new SERVER-side format
roll behind ``GET /teacher/exercise``, for ALL users, not just developers:

- :func:`forge_item_for_pattern` — the SAME draft -> sanity-gate -> verify ->
  one-redraft -> raise loop as :func:`forge_item` above, except the seed is a
  TAXONOMY ENTRY (label/description/wrong/right/elicit) instead of a
  free-text topic, and the draft prompt is pitched at the learner's CEFR
  level via a ``{level_block}`` slot (:data:`PRODUCE_DRAFT_PATTERN_PROMPT`).
  Two structured-output LLM calls, same as the topic-seeded path — this is
  the ONE format in CLARA-17 that costs anything, which is why the dealer
  gates it behind the shared drill throttle before ever calling in here.
- :func:`build_redo_item` — no LLM at all: builds one item straight from a
  single example already sitting in the learner's own ``user_errors`` ledger
  (``database/orm.py::UserError.examples``) — their own past wrong sentence,
  handed back for them to correct. Every field it uses already came from a
  judge (the ledger example) or the curated taxonomy, so there is nothing
  left to verify.

Both return the SAME internal item shape :func:`forge_item` does (plus
``pattern_id`` on the produce one) and are graded by the SAME
:func:`grade_produced` through the SAME attempts routes — no new grading
code this round. Real learners reach both of these; only the free-text
``ÜBUNG-NEU`` forge above stays developer-only.

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
from grammar import bucket_of  # CLARA-17: normalizes a learner's raw level string

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
# Draft + verify + judge schemas. Cerebras strict json_schema limits apply
# (see CLAUDE.md): root object, additionalProperties:false (langchain's
# to_strict_json_schema adds this automatically for a plain pydantic model,
# same as every sibling judge schema in this repo — no manual model_config
# needed), no pattern/format/minItems/maxItems/minLength/maxLength. Any
# "at most N words" phrasing below is prompt guidance, not a schema
# constraint — Cerebras strict mode forbids enforcing it in the schema
# itself.
# --------------------------------------------------------------------------


class ProduceDraft(BaseModel):
    """One drafted German production task for a free-text topic — an
    English instruction the student answers with ONE original German
    sentence of their own, never a gap to fill and never a sentence to
    translate."""

    task_en: str = Field(
        description=(
            "One-sentence ENGLISH instruction telling the student what to "
            "produce — a concrete, everyday scenario that REQUIRES the "
            "topic's structure (not merely allows it), e.g. for hätte-"
            "wishes: 'Make a wish about your weekend using \"hätte\".' "
            "Must be answerable with exactly ONE German sentence"
        )
    )
    target_de: str = Field(
        description="The exact German word(s)/structure the sentence must contain — short, this is what the frontend shows bold"
    )
    example_de: str = Field(
        description=(
            "ONE natural German sentence that fully satisfies the task — a "
            "model answer a real student could give. A2-B1 vocabulary "
            "unless the topic itself genuinely needs higher"
        )
    )
    rule_note: str = Field(
        description="One short English sentence naming the grammar rule the task practices"
    )
    title: str = Field(description="Two to four words naming the exercise topic")


class ProduceVerify(BaseModel):
    """Blind second-pass check on a drafted production task: answer the
    task yourself, from scratch, before trusting anything the draft
    claimed."""

    own_answer: str = Field(
        description="Your OWN one-sentence German answer to the task, written BEFORE weighing the draft's example_de"
    )
    ok: bool = Field(
        description=(
            "True iff ALL of: the task is clear and doable in exactly one "
            "German sentence; the target genuinely belongs in ANY correct "
            "answer to it (truly unavoidable, not just one option among "
            "several); and the draft's example_de fully satisfies the task "
            "and is correct, natural German"
        )
    )
    reason: str = Field(description="One short line: why ok is false, or 'ok' when true")


class ProduceVerdict(BaseModel):
    """Verdict on a learner's own sentence answering a live-forged
    production task — constraint-judged free production, with NO
    accept-list: the judge grades the sentence directly against the task
    and the target structure, never against a fixed list of pre-approved
    fillers."""

    correct: bool = Field(
        description="True iff the sentence answers the task AND uses the target structure correctly"
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "REQUIRED when correct is false — at most 14 words naming the "
            "miss. When correct is true, may carry one tiny by-the-way tip "
            "unrelated to the target, or stay null"
        ),
    )
    corrected: Optional[str] = Field(
        default=None,
        description=(
            "The learner's OWN sentence, minimally repaired to satisfy the "
            "task with the target — only when correct is false. Never a "
            "different sentence, never the reference answer verbatim. Null "
            "when correct"
        ),
    )


PRODUCE_DRAFT_PROMPT = """# Role
You draft ONE production task for a German student who asked their teacher about a topic, live, on the spot. A production task is an English instruction telling the student to write or say ONE German sentence of their own — never a fill-the-blank, never a sentence to translate.

# Topic
{topic}

# What to build
- `task_en` — ONE English instruction describing an everyday, concrete scenario the student could plausibly be in, phrased so that producing it in German REQUIRES the topic's structure — not merely allows it. Example: for a Konjunktiv II wish, "Make a wish about your weekend using 'hätte'." The scenario must be answerable with exactly ONE complete German sentence.
- `target_de` — the exact German word(s) or structure the sentence must contain — short, this is what the frontend shows bold as the student's hint.
- `example_de` — ONE natural German sentence that fully satisfies the task — a model answer a real student could give. A2-B1 vocabulary throughout, unless the topic itself genuinely needs higher.
- `rule_note` — ONE short English sentence naming the rule the task practices.
- `title` — two to four words naming the exercise.

# Hard rules
- `task_en` is IN ENGLISH — never write the instruction itself in German.
- The task must be answerable with exactly ONE German sentence — not a paragraph, not a list, not several sentences.
- The target must be UNAVOIDABLE: a student who genuinely completes the scenario cannot dodge the structure and still succeed. If a task CAN be answered without the target, tighten the scenario until the structure is the only natural way to say it.
- Never a fill-the-blank (no ___ anywhere in `task_en`) and never a translate-this-sentence task — the student invents their own sentence from the scenario; they never convert one that's handed to them.
- Everyday, concrete, and natural — a real teacher's assignment, not a stilted textbook drill sentence.
"""

# --------------------------------------------------------------------------
# CLARA-17: the pattern-seeded sibling of PRODUCE_DRAFT_PROMPT above — same
# schema (ProduceDraft), same hard rules, but the seed is a TAXONOMY ENTRY
# (label/description/wrong-right/elicit) rather than a free-text topic, and
# the vocabulary/complexity pitch is a `{level_block}` slot instead of the
# hardcoded "A2-B1 vocabulary" line, so the SAME pattern reads differently
# for an A1 beginner than for a B2+ learner. `_level_block` below selects
# the text; the four blocks are demonstration-flavored guidance rather than
# a rule list on purpose — this repo's gpt-oss experience (CLAUDE.md) is
# that worked examples beat abstract prohibitions, hence the WRONG/RIGHT
# pair inside the B2+ block.
# --------------------------------------------------------------------------

PRODUCE_DRAFT_PATTERN_PROMPT = """# Role
You draft ONE production task for a German student, seeded by a specific grammar pattern from the curriculum — not a free-text topic. A production task is an English instruction telling the student to write or say ONE German sentence of their own — never a fill-the-blank, never a sentence to translate.

# Pattern
- label: {label}
- rule: {description}
- minimal contrast: "{wrong}" is WRONG, "{right}" is RIGHT
- how a conversation partner naturally elicits it: {elicit}

# What to build
- `task_en` — ONE English instruction describing an everyday, concrete scenario the student could plausibly be in, phrased so that producing it in German REQUIRES the pattern above — not merely allows it. Use the elicit hint as inspiration for a natural scenario, and the wrong/right contrast to know exactly what must go right. The scenario must be answerable with exactly ONE complete German sentence.
- `target_de` — the exact German word(s) or structure the sentence must contain — short, this is what the frontend shows bold as the student's hint. Draw this from the pattern's right-form contrast, not a paraphrase of the label.
- `example_de` — ONE natural German sentence that fully satisfies the task — a model answer a real student could give. {level_block}
- `rule_note` — ONE short English sentence naming the rule the task practices (may reuse the rule above, tightened to one line).
- `title` — two to four words naming the exercise.

# Hard rules
- `task_en` is IN ENGLISH — never write the instruction itself in German.
- The task must be answerable with exactly ONE German sentence — not a paragraph, not a list, not several sentences.
- The target must be UNAVOIDABLE: a student who genuinely completes the scenario cannot dodge the structure and still succeed. If a task CAN be answered without the target, tighten the scenario until the structure is the only natural way to say it.
- Never a fill-the-blank (no ___ anywhere in `task_en`) and never a translate-this-sentence task — the student invents their own sentence from the scenario; they never convert one that's handed to them.
- Everyday, concrete, and natural — a real teacher's assignment, not a stilted textbook drill sentence.
"""

# The four `{level_block}` texts, keyed by grammar.levels.BUCKETS. A1 keeps
# PRODUCE_DRAFT_PROMPT's original, unconditional "A2-B1 vocabulary" line —
# that's the pre-CLARA-17 default, and it's also what a None level (a
# learner who never answered the level question) still gets.
_LEVEL_BLOCK_A1 = "A2-B1 vocabulary throughout, unless the topic itself genuinely needs higher."

_LEVEL_BLOCK_A2 = (
    "Aim for an everyday CONNECTED sentence, not a bare subject-verb-object line — the "
    "scenario should naturally pull in a reason (weil/deshalb) or a time expression "
    "alongside the target. Still A2-B1 vocabulary."
)

_LEVEL_BLOCK_B1 = (
    "Demand more than a single main clause: the scenario should require a subordinate "
    "clause or a real connector (weil, obwohl, nachdem, wenn, damit...) to answer "
    "naturally — a student who gets away with one bare main clause has dodged the "
    "point. Both Perfekt and Präteritum are fair game wherever the scenario calls for "
    "a past tense."
)

_LEVEL_BLOCK_B2 = (
    "Demand a genuinely complex sentence — layered clauses, natural idiom, nothing a "
    "beginner could stumble into by accident. The scenario itself needs real "
    "substance: a three-word answer must be structurally impossible, not merely "
    "unlikely. For example, for a Konjunktiv II regret: a WEAK task like \"Say you "
    "would have liked more coffee\" is answerable in three words with no real "
    "scenario — don't write that. A STRONG task instead: \"A colleague just told you "
    "they got the promotion you both applied for. Tell a friend afterward what you "
    "would have done differently if you'd known the deadline was that week.\" — that "
    "forces a layered Konjunktiv II Vergangenheit sentence, not a reflex phrase."
)

_LEVEL_BLOCKS = {"A1": _LEVEL_BLOCK_A1, "A2": _LEVEL_BLOCK_A2, "B1": _LEVEL_BLOCK_B1, "B2+": _LEVEL_BLOCK_B2}


def _level_block(level: str | None) -> str:
    """Selects the `{level_block}` slot text for PRODUCE_DRAFT_PATTERN_PROMPT.
    Normalizes any raw CEFR-ish string through ``grammar.bucket_of`` (``"B2"``,
    ``"b1 "``, ``None`` all land on one of the four blocks); an unknown or
    absent level falls to the A1 block, same "serve the pre-personalization
    default" behavior every other level-aware caller in this repo uses for a
    learner who hasn't set one (``grammar/levels.py``, ``drills/leveling.py``).
    """
    return _LEVEL_BLOCKS.get(bucket_of(level), _LEVEL_BLOCK_A1)


PRODUCE_VERIFY_PROMPT = """# Role
You are a strict second pass on a live-forged German production task, before it ever reaches a student. Answer the task YOURSELF first — do not simply trust the draft.

# The item
- task (English instruction): "{task_en}"
- target structure the answer must contain: "{target_de}"
- rule being tested: {rule_note}
- draft's own model answer: "{example_de}"

# What to do
1. Read ONLY the task. Write your own one-sentence German answer to it in `own_answer` — before weighing anything the draft claimed.
2. Check three things, in order:
   - Is the task clear and doable with ONE German sentence — not vague, not requiring more?
   - Does the target genuinely belong in ANY correct answer to this task — is it truly unavoidable, not just one option among several?
   - Does the draft's `example_de` fully satisfy the task, and is it correct, natural German?
3. Set `ok` to true only if all three hold. Otherwise false, with `reason` naming the problem in one short line (else "ok").
"""

# CLARA-16: the spoken-attempt tolerance rules, injected as `{modality_block}`
# in PRODUCE_JUDGE_PROMPT when grading a transcribed answer — same "the
# learner never chose the spelling" convention every ASR-fed judge in this
# repo follows (see CLAUDE.md, faelle/judge.py's style, satz/examiner.py's
# PROMPT). For a typed answer, `_modality_block` substitutes a one-line
# opposite instead: spelling counts, punctuation doesn't.
SPOKEN_TOLERANCE_BLOCK = """# How this sentence arrived
The sentence arrived via speech recognition — the learner never chose the spelling. Ignore punctuation, capitalization and spelling entirely; resolve homophones in the learner's favor (das/dass, seid/seit, wieder/wider). A trimmed word ending is the recognizer's doing, not the learner's. Grade word choice and structure only."""

_TYPED_TOLERANCE_LINE = (
    "# How this sentence arrived\nThe sentence was typed; spelling counts, but punctuation does not."
)


def _modality_block(spoken: bool) -> str:
    """Selects the `{modality_block}` slot text for PRODUCE_JUDGE_PROMPT —
    the full spoken-tolerance rules (SPOKEN_TOLERANCE_BLOCK) for a
    transcribed answer, or the one-line typed equivalent otherwise."""
    return SPOKEN_TOLERANCE_BLOCK if spoken else _TYPED_TOLERANCE_LINE


PRODUCE_JUDGE_PROMPT = """# Role
You grade ONE learner sentence against a live-forged German production task. There is no accept-list here — the learner invented their own sentence, so you judge it directly against the task, never against the reference answer word-for-word.

# The task
- instruction: "{task_en}"
- target structure that must appear, used correctly: "{target_de}"
- the rule being practiced: {rule_note}
- one good answer (a reference, NOT the only one): "{example}"

{modality_block}

# What the learner wrote
"{sentence}"

# STEP 1 — isolate what you grade
Grade ONLY two things:
1. Does the sentence answer the TASK — is it a plausible, complete response to the scenario?
2. Is the TARGET structure present in the sentence and used CORRECTLY?
An unrelated slip anywhere ELSE in the sentence — a different word choice, a minor typo, an equally valid way of phrasing the rest — must NEVER flip `correct` to false. Note it as a small tip if you like, nothing more.

# STEP 2 — worked examples
- task "Make a wish about your weekend using 'hätte'" · target "hätte" · reference "Ich hätte gern mehr Zeit für meine Familie gehabt." · learner "Ich hätte gerne mal wieder richtig ausgeschlafen." → **correct: true**. A completely different sentence from the reference, still a genuine wish, `hätte` used correctly — that's the whole point of free production.
- same task/target · learner "Ich würde gerne mal wieder richtig ausschlafen, wen ich mehr zeit hätte." → **correct: true**, note: "small typo: 'wen' should be 'wenn' — otherwise spot on". A stray typo outside the target is not a grammar failure.
- same task/target · learner "Ich habe letztes Wochenende viel geschlafen." → **correct: false**. CONTROL — `hätte` never appears at all, the target structure is simply missing. note: "no 'hätte' — that's the whole point of a wish", corrected: "Ich hätte letztes Wochenende gerne viel geschlafen."
- task "Explain what you would do if you won the lottery, using a Konjunktiv II sentence with 'würde'" · target "würde" · learner "Wenn ich im Lotto gewinne, kaufe ich ein Haus." → **correct: false**. DODGE — the learner answered with a real-condition sentence instead of the hypothetical the task asked for; `würde` (or an equivalent Konjunktiv II form) never appears. note: "that's a real condition, not the hypothetical 'würde' asks for", corrected: "Wenn ich im Lotto gewinnen würde, würde ich ein Haus kaufen."
- task "Tell a friend they should see a doctor, using 'solltest'" · target "solltest" · learner "Du solltest zum Arzt gehen." → **correct: true**. Answers the task, target present and correctly formed.

# STEP 3 — grade
- `correct` — true iff BOTH step-1 conditions pass.
- `note` — REQUIRED when correct=false, AT MOST 14 words naming the miss (missing target, target misused, or doesn't answer the task). When correct=true, may carry one tiny by-the-way tip (a typo, a stylistic aside) or stay null — never invent a problem just to fill it.
- `corrected` — ONLY when correct=false: the learner's OWN sentence, minimally repaired to satisfy the task with the target — never a different sentence, never the reference answer verbatim.
"""


async def _draft(topic: str, *, reason: Optional[str] = None) -> ProduceDraft:
    # JUDGE-001 (2026-08-15): drafts CONTENT, not a verdict — temperature=None
    # keeps provider-default sampling, same convention as drills/forge.py's
    # draft calls and satz/enricher.py.
    llm = structured_judge_llm(ProduceDraft, temperature=None)
    prompt = PRODUCE_DRAFT_PROMPT.replace("{topic}", topic)
    if reason:
        prompt += (
            f"\n\n# Your last attempt was rejected\n{reason}\n"
            f"Fix exactly that; keep everything else that was fine.\n"
        )
    with generation_span("teacher-forge-draft", model=FORGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result


async def _draft_pattern(entry: dict, level: str | None, *, reason: Optional[str] = None) -> ProduceDraft:
    """CLARA-17 sibling of :func:`_draft`: same schema, same span name
    (format-agnostic — it names the STAGE, not the seed), but the prompt is
    seeded by a taxonomy ENTRY plus a level pitch instead of a free-text
    topic. Same "drafts CONTENT, not a verdict" temperature=None rationale
    as :func:`_draft`.
    """
    llm = structured_judge_llm(ProduceDraft, temperature=None)
    prompt = (
        PRODUCE_DRAFT_PATTERN_PROMPT.replace("{label}", entry["label"])
        .replace("{description}", entry["description"])
        .replace("{wrong}", entry["wrong"])
        .replace("{right}", entry["right"])
        .replace("{elicit}", entry["elicit"])
        .replace("{level_block}", _level_block(level))
    )
    if reason:
        prompt += (
            f"\n\n# Your last attempt was rejected\n{reason}\n"
            f"Fix exactly that; keep everything else that was fine.\n"
        )
    with generation_span("teacher-forge-draft", model=FORGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result


async def _verify(draft: ProduceDraft) -> ProduceVerify:
    # temperature=0 default (structured_judge_llm) — this IS a verdict.
    llm = structured_judge_llm(ProduceVerify)
    prompt = (
        PRODUCE_VERIFY_PROMPT.replace("{task_en}", draft.task_en)
        .replace("{target_de}", draft.target_de)
        .replace("{rule_note}", draft.rule_note)
        .replace("{example_de}", draft.example_de)
    )
    with generation_span("teacher-forge-verify", model=FORGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result


def _sane_produce(draft: ProduceDraft) -> Optional[str]:
    """Cheap structural gate BEFORE ever calling the verify LLM — a reason
    string when something's off (fed back into the redraft), else None.

    Field-emptiness plus a lenient token-containment check: at least one
    whitespace-token of `target_de` must appear (case-insensitively) inside
    `example_de`. Exact-string containment would false-negative on
    inflection (`target_de` "hätte" vs. an example that only ever inflects
    it further, or a multi-word target where only part shows up unchanged)
    — checking tokens individually and passing on ANY hit is the same
    lenient-containment tradeoff v1's `_sane` made for its gap/accepts
    check, ported to the produce shape.
    """
    fields = {
        "task_en": draft.task_en,
        "target_de": draft.target_de,
        "example_de": draft.example_de,
        "rule_note": draft.rule_note,
        "title": draft.title,
    }
    for name, value in fields.items():
        if not value.strip():
            return f"{name} must not be empty"

    target_tokens = [t for t in draft.target_de.strip().lower().split() if t]
    example_lower = draft.example_de.strip().lower()
    if target_tokens and not any(tok in example_lower for tok in target_tokens):
        return "example_de must contain the target structure"
    return None


async def forge_item(topic: str) -> dict:
    """Draft -> verify ONE production task for ``topic``. One redraft on a
    rejected first pass (sanity gate OR verify), then raises.

    Returns an internal dict — id/topic/title/task/target/example/rule_note
    — that ``teacher/routes.py`` both stores whole (via :func:`store_item`,
    for grading) and projects down to the served pre-attempt shape
    (``{id, task, target, hint}``) — ``example`` (the model answer) is
    withheld until after the attempt; serving it up front would hand the
    learner the answer.
    """
    reason: Optional[str] = None
    for attempt in (1, 2):
        draft = await _draft(topic, reason=reason)
        gate_reason = _sane_produce(draft)
        if gate_reason is not None:
            logger.warning(f"[FORGE] draft #{attempt} for {topic!r} failed sanity gate: {gate_reason}")
            reason = gate_reason
            continue

        verify = await _verify(draft)
        if verify.ok:
            return {
                "id": f"forge-{uuid4().hex}",
                "topic": topic,
                "title": draft.title,
                "task": draft.task_en,
                "target": draft.target_de,
                "example": draft.example_de,
                "rule_note": draft.rule_note,
            }

        logger.warning(f"[FORGE] draft #{attempt} for {topic!r} rejected by verify: {verify.reason}")
        reason = verify.reason

    raise RuntimeError(f"forge_item: exhausted retries for topic={topic!r} last_reason={reason!r}")


async def forge_item_for_pattern(entry: dict, level: str | None) -> dict:
    """CLARA-17 sibling of :func:`forge_item`: draft -> sanity-gate -> verify
    -> one-redraft -> raise, IDENTICAL control flow, except the seed is a
    TAXONOMY ENTRY (:func:`_draft_pattern`) rather than a free-text topic,
    pitched at ``level`` (:func:`_level_block`). Reuses :func:`_sane_produce`
    and :func:`_verify` verbatim — the sanity gate and the verify pass don't
    care where the draft came from.

    Returns the SAME internal dict shape :func:`forge_item` does, PLUS
    ``pattern_id`` — ``teacher/dealer.py`` needs it to store/attribute the
    item; ``topic`` is set to the entry's own ``label`` (an English
    structure name, e.g. "Verb-second word order"), matching what the
    dealer serves as the exercise's ``topic``.
    """
    reason: Optional[str] = None
    for attempt in (1, 2):
        draft = await _draft_pattern(entry, level, reason=reason)
        gate_reason = _sane_produce(draft)
        if gate_reason is not None:
            logger.warning(
                f"[FORGE] pattern draft #{attempt} for {entry['id']!r} failed sanity gate: {gate_reason}"
            )
            reason = gate_reason
            continue

        verify = await _verify(draft)
        if verify.ok:
            return {
                "id": f"forge-{uuid4().hex}",
                "topic": entry["label"],
                "pattern_id": entry["id"],
                "title": draft.title,
                "task": draft.task_en,
                "target": draft.target_de,
                "example": draft.example_de,
                "rule_note": draft.rule_note,
            }

        logger.warning(
            f"[FORGE] pattern draft #{attempt} for {entry['id']!r} rejected by verify: {verify.reason}"
        )
        reason = verify.reason

    raise RuntimeError(
        f"forge_item_for_pattern: exhausted retries for pattern={entry['id']!r} last_reason={reason!r}"
    )


def build_redo_item(entry: dict, example: dict, level: str | None) -> dict:
    """CLARA-17's third format, built with NO LLM at all: one item straight
    from a single example already sitting in the learner's own
    ``user_errors`` ledger (``database/orm.py::UserError.examples`` — a dict
    with ``sentence``/``corrected``/optional ``note``, produced by whichever
    judge classified the original slip). The learner's OWN wrong sentence
    goes into the task VERBATIM — it's their own data, not content this
    module generated.

    ``level`` is accepted for signature symmetry with
    :func:`forge_item_for_pattern` (``teacher/dealer.py`` passes the same
    value to both generators) but doesn't change anything here — the
    learner's own past sentence already sets its own difficulty; there's no
    vocabulary knob to turn on someone else's words.

    No verify pass, unlike the LLM-drafted produce format: every piece here
    came from either a judge (the ledger example) or the curated taxonomy
    already, so there's nothing left to sanity-check.
    """
    return {
        "id": f"forge-{uuid4().hex}",
        "topic": entry["label"],
        "pattern_id": entry["id"],
        "title": "Fix your sentence",
        "task": (
            f'Earlier you said: "{example["sentence"]}" — '
            f'{example.get("note") or entry["description"]}. '
            "Say or write it correctly, as one complete German sentence."
        ),
        "target": entry["label"],
        "example": example["corrected"],
        "rule_note": example.get("note") or entry["description"],
    }


async def _judge_produced(item: dict, sentence: str, *, spoken: bool) -> ProduceVerdict:
    # temperature=0 default (structured_judge_llm) — this IS a verdict.
    llm = structured_judge_llm(ProduceVerdict)
    prompt = (
        PRODUCE_JUDGE_PROMPT.replace("{task_en}", item["task"])
        .replace("{target_de}", item["target"])
        .replace("{rule_note}", item["rule_note"])
        .replace("{example}", item["example"])
        .replace("{modality_block}", _modality_block(spoken))
        .replace("{sentence}", sentence)
    )
    with generation_span("teacher-forge-judge", model=FORGE_MODEL, input_text=prompt) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    if result.correct:
        result.corrected = None
    return result


async def grade_produced(
    item: dict, answer: str, *, spoken: bool = False, give_up: bool = False
) -> tuple[dict, bool]:
    """Grade one learner sentence against a forged production ``item``.
    Returns ``(verdict, judge_skipped)`` — the same two-element shape every
    sibling drill's ``grade`` returns.

    Unlike v1's gap-fill (a deterministic accepts-match short-circuited the
    judge on a hit), constraint-judged free production has NO accept-list to
    match against — a real attempt is ALWAYS judged, the same "no
    deterministic path" contract ``sprechen/grading.py::grade`` already
    uses. ``judge_skipped`` is True only for a give-up, the one case that
    never calls the judge at all.

    A judge-call exception PROPAGATES rather than failing soft — unlike v1's
    ``grade_forged`` (which had a deterministic miss-verdict to fall back
    on), there is no fallback verdict for free production: the caller
    (``teacher/routes.py``) maps the exception to a 502 JUDGE_UNAVAILABLE.

    An empty/whitespace ``answer`` is not handled here — the ROUTE 422s that
    before this is ever called (both the typed and the audio-transcript
    paths validate non-empty input before reaching this function).
    """
    if give_up:
        return (
            {
                "correct": False,
                "note": item["rule_note"],
                "corrected": None,
                "example": item["example"],
                "gaveUp": True,
            },
            True,
        )

    sentence = " ".join(answer.split())
    verdict = await _judge_produced(item, sentence, spoken=spoken)
    return (
        {
            "correct": verdict.correct,
            "note": verdict.note,
            "corrected": verdict.corrected,
            "example": item["example"],
        },
        False,
    )
