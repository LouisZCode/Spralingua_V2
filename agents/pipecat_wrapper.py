"""
ClientWrapper: makes the LangChain agent compatible with Pipecat's LangchainProcessor.

Each client connection gets its own ClientWrapper instance, holding:
- agent: a fresh LangChain agent with its own InMemorySaver
- user_id: unique thread_id for conversation memory
"""

import asyncio
import difflib
import re
import time

from loguru import logger
from pipecat.processors.frameworks.rtvi import (
    RTVIBotOutputMessage,
    RTVIBotOutputMessageData,
)
from pipecat.utils.tracing.turn_context_provider import get_current_turn_context
from pydantic import ConfigDict


class _BotOutputDataWithDuration(RTVIBotOutputMessageData):
    """Bot-output payload that carries the turn's TTS audio duration.

    Pipecat's ``RTVIBotOutputMessageData`` defaults to Pydantic's ``extra=ignore``,
    so adding ``audio_duration_ms`` as a free field would be silently dropped on
    serialization. ``extra="allow"`` opts in to round-tripping our extra so the
    frontend can read it and schedule the bubble reveal after audio playback.
    """

    model_config = ConfigDict(extra="allow")
    audio_duration_ms: float


class _BotOutputMessageWithDuration(RTVIBotOutputMessage):
    """Bot-output envelope whose ``data`` is typed as the duration-aware subclass.

    Without this override the outer model's field type is the *base*
    ``RTVIBotOutputMessageData`` and Pydantic uses *that* schema when serializing,
    which strips the ``audio_duration_ms`` field even when the instance is the
    subclass. Pointing the field at the subclass keeps the extra during
    ``model_dump()``.
    """

    data: _BotOutputDataWithDuration

from config.settings import conversation_first_token_s
from grammar.loader import load_explanations, load_taxonomy

from .conversation_agent import agent_assembly, CONVERSATIONAL_MODEL
from .dynamic_prompts import Context, get_last_system_prompt
from .fake_profiles import load_profile
from .load_prompts import load_prompts
from .observability import get_current_turn_span, mark_span_error, tracer

# `max_exchanges` is now per-lesson, read from the YAML in `ClientWrapper.__init__`.
# End-of-call fires when either the count cap (max_exchanges) is reached OR a
# goodbye phrase appears in the agent's reply — whichever comes first. Goodbye
# detection is only armed in the lesson's final exchange (see _goodbye_after),
# so an opening line like "great to see you" can't end the call on turn 1.

GOODBYE_PHRASES = [
    # English (welcome concierge + English drills)
    "goodbye", "bye", "see you", "take care",
    "nice talking", "great talking", "good talking",
    "talk to you later", "talk soon", "have a good",
    "have a nice", "it was nice meeting",
    # German (the German runtime — open chat, drills, tandem). Without these a
    # German farewell ("Tschüss") would never trip the goodbye-driven disconnect,
    # so a German session could only ever end on the max_exchanges cap.
    "tschüss", "tschüs", "tschau", "ciao",
    "auf wiedersehen", "wiedersehen",
    "bis bald", "bis später", "bis dann", "bis morgen",
    "bis zum nächsten mal", "wir sehen uns",
    "mach's gut", "machs gut", "pass auf dich auf",
    "schönen tag", "schönes wochenende", "gute nacht", "alles gute",
]

# CHORE-001: plain substring matching could false-positive inside a larger word
# (e.g. "vorbye"); \b is Unicode-aware so umlaut phrases (e.g. "tschüss") still
# match correctly.
_GOODBYE_RE = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in GOODBYE_PHRASES) + r")\b")


def _contains_goodbye(text: str) -> bool:
    return bool(_GOODBYE_RE.search(text.lower()))


# PERF-005: spoken fallback when the LLM produces no first token twice in a
# row (see the retry loop in ClientWrapper.astream). Picked so neither line
# contains any GOODBYE_PHRASES entry — a fallback that accidentally tripped
# goodbye detection would silently end the session on the exact turn where
# the learner most needs it to keep going. German for tandem/conversation/
# respond (the German runtime); English for teacher (Clara speaks English).
_FALLBACK_REPLY_DE = (
    "Entschuldige, ich habe dich gerade nicht verstanden — sag das bitte noch einmal."
)
_FALLBACK_REPLY_EN = "Sorry, I didn't quite catch that — could you say it again?"


# REL-002: stage-direction sentinel injected as a synthetic user turn during
# a SIGTERM drain (see pipeline/factory.py::_wrap_up_turn / begin_drain, and
# ClientWrapper.request_wrap_up below). Written as an instruction TO the
# model, in English, the same way _augment_tandem_last_turn's own stage
# direction below is — an English instruction reads fine even inside a
# German session, and naming the target language explicitly removes any
# ambiguity about which language the actual goodbye sentence should land in.
# German for tandem/conversation/respond (the German runtime); English for
# teacher (Clara speaks English) — the same _is_teacher_lesson split
# ClientWrapper already uses to pick _FALLBACK_REPLY_DE/EN above.
_WRAP_UP_SENTINEL_DE = (
    "(Stage direction — this session must end immediately for technical "
    "reasons. Briefly react to what was just said, then say one short, "
    "warm goodbye sentence in German and close the conversation — ask no "
    "new question of your own.)"
)
_WRAP_UP_SENTINEL_EN = (
    "(Stage direction — this session must end immediately for technical "
    "reasons. Briefly react to what was just said, then say one short, "
    "warm goodbye sentence in English and close the conversation — ask no "
    "new question of your own.)"
)


# AGENT-00X: Clara's interactive-exercise loop. `teacher.yaml`'s "Practice
# items" section teaches the model to end a reply with this exact marker —
# see teacher/routes.py for what happens once the pattern id reaches the
# frontend. `ExerciseMarkerFilter` below is the ONLY thing standing between
# the raw token stream and TTS/transcript/pending bot text, so no fragment of
# the marker — not even a lone "[" — is ever spoken, stored, or shown.
#
# CLARA-15 P3: generalized from the single `[[ÜBUNG: <id>]]` deal marker to
# THREE markers sharing the `[[ÜBUNG` stem, distinguished by what follows it:
#   [[ÜBUNG: <id>]]          — deal (unchanged: slug id, existing behavior)
#   [[ÜBUNGSWUNSCH: <text>]] — demand-only signal (free text, no id printed)
#   [[ÜBUNG-NEU: <text>]]    — dev-only live-forge deal (free text)
# The three prefixes diverge at the character right after the shared "[[ÜBUNG"
# stem (':' vs 'S' vs '-'), so none is ever a prefix of another once that
# point is reached — the streaming matcher below narrows the set of still-
# possible prefixes as each character arrives, with no ambiguity.
EXERCISE_MARKER_PREFIX = "[[ÜBUNG:"
EXERCISE_MARKER_WUNSCH_PREFIX = "[[ÜBUNGSWUNSCH:"
EXERCISE_MARKER_NEU_PREFIX = "[[ÜBUNG-NEU:"
EXERCISE_MARKER_CLOSE = "]]"
# kind -> full opening prefix, in priority-irrelevant order (the streaming
# matcher below disambiguates purely by character divergence, not by order).
_EXERCISE_MARKER_PREFIXES = {
    "deal": EXERCISE_MARKER_PREFIX,
    "wunsch": EXERCISE_MARKER_WUNSCH_PREFIX,
    "neu": EXERCISE_MARKER_NEU_PREFIX,
}
# Taxonomy ids are lowercase-hyphenated slugs (grammar/taxonomy.yaml); a
# marker id that doesn't look like one is dropped rather than pushed —
# never trust free-form model output as a value going straight to the client.
# Applies ONLY to the `deal` kind — `wunsch`/`neu` carry free-text topics
# (spaces, umlauts) and are validated separately (length only) where they're
# consumed, in `ClientWrapper.astream`'s finally block.
_EXERCISE_MARKER_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def _snap_pattern_id(pattern_id: str, known_ids: set[str]) -> str:
    """CLARA-16: snap a near-miss deal-marker id onto the printed set.

    The catalog probe caught Clara anglicizing a printed id one character off
    (`passive-werden` for the printed `passiv-werden`) — the marker was valid
    as a slug, so it sailed through to `GET /teacher/exercise`, 404ed there,
    and the learner would have seen an announced exercise never arrive. The
    wrapper knows every id actually printed on her page (`Context.
    grammar_focus` + `Context.exercise_catalog`), so a close-but-wrong id is
    corrected deterministically here, BEFORE the RTVI push. Only near misses
    snap (difflib cutoff 0.8 — one-letter slips and anglicized spellings); a
    genuinely hallucinated id passes through unchanged so the deal route's
    404 + ERROR trace keeps its agent-quality-signal meaning. With no known
    ids at all (both layers empty/unloaded), nothing snaps.
    """
    if not known_ids or pattern_id in known_ids:
        return pattern_id
    close = difflib.get_close_matches(pattern_id, known_ids, n=1, cutoff=0.8)
    if close:
        logger.info(
            f"[EXERCISE] Snapped near-miss marker id {pattern_id!r} -> {close[0]!r}"
        )
        return close[0]
    return pattern_id


# Matches grammar/taxonomy.yaml's own id transliteration convention
# (ä/ö/ü/ß spelled out ASCII, e.g. "wechselpraepositionen") so a learner
# typing the real German word in the free-text box still hits its id.
_UMLAUT_TRANSLITERATION = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
)


def _match_taxonomy_pattern(topic: str) -> str | None:
    """AGENT-005 follow-up / TOPIC-FREEFORM: best-effort match of a free-text
    Clara topic against `grammar/taxonomy.yaml`'s ids and labels — NOT a
    grading step, just a signal for the weekly demand read-out to group by
    ("this free-text ask was probably about a pattern we already cover").
    Case-insensitive, umlaut-tolerant: an exact id/label match wins first
    (checked against both the raw text and an ASCII-transliterated form —
    taxonomy ids are themselves transliterated, e.g. `wechselpraepositionen`
    for "Wechselpräpositionen", so a learner typing the real German word
    would otherwise never hit), then a substring match either direction
    (catches "dative prepositions" against a "Dative prepositions" label).
    Returns None on no match — never raises, so a caller can treat any
    exception from `load_taxonomy()` the same as "no match" rather than
    fail the log.
    """
    needle = topic.strip().lower()
    if not needle:
        return None
    try:
        taxonomy = load_taxonomy()
    except Exception:  # noqa: BLE001 — matching is best-effort, never fatal
        return None
    needle_ascii = needle.translate(_UMLAUT_TRANSLITERATION)
    for pattern_id, entry in taxonomy.items():
        pid = pattern_id.lower()
        label = (entry.get("label") or "").lower()
        if needle == pid or needle_ascii == pid or needle == label:
            return pattern_id
    for pattern_id, entry in taxonomy.items():
        label = (entry.get("label") or "").lower()
        if label and (label in needle or needle in label):
            return pattern_id
    return None


async def log_topic_freeform(topic: str, *, user_id: str, session_id: str) -> None:
    """AGENT-005 follow-up: log a free-text Clara topic — one typed into the
    "Or ask about anything else..." box on the topic screen rather than a
    tapped focus/starter card. A tapped card always carries a validated
    `?pattern=` (see `pipeline/factory.py`'s `picked_pattern`); the caller is
    the one place that knows the two apart, and calls this ONLY when
    `picked_pattern` is None and the topic string is non-empty.

    Mirrors CLARA-15's `_log_exercise_demand` conventions (one span + one
    loguru line, the same attribute names, the same DEFAULT Langfuse
    environment — never "forge", this is learner-facing) so
    `speedtest/demand_readout.py` can read both kinds the same way. One
    deliberate difference: `_log_exercise_demand` nests under the current
    turn's trace, while this fires at connect time before any turn exists,
    so it is its own root span (session id + user id still attached via
    the baggage processor and the explicit attributes below): "log first, build/pair only where demand shows" now
    extends from missing exercises to missing pool topics. Called once per
    admitted teacher connect from `pipeline/factory.py`, AFTER the daily-talk
    gate — a rejected connect (4002/4003/1011) never reaches this call, so
    nothing is logged for it. Fail-soft: any exception here must never break
    the connect.
    """
    try:
        matched = _match_taxonomy_pattern(topic)
        with tracer.start_as_current_span("teacher-topic-freeform") as span:
            span.set_attribute("user.id", user_id)
            span.set_attribute("langfuse.session.id", session_id)
            span.set_attribute("langfuse.observation.input", topic)
            span.set_attribute("langfuse.observation.metadata.topic", topic)
            span.set_attribute("langfuse.observation.metadata.kind", "free")
            # OTel span attributes can't carry a literal None — omit the key
            # rather than write one; demand_readout.py treats an absent
            # `matched_pattern` as "no match", same convention as its
            # existing `topic`/`kind` `.get(..., "?")` fallbacks.
            if matched is not None:
                span.set_attribute(
                    "langfuse.observation.metadata.matched_pattern", matched
                )
        logger.info(
            f"[TOPIC-FREEFORM] user={user_id!r} topic={topic!r} matched={matched!r}"
        )
    except Exception as e:  # noqa: BLE001 — this logging must never break a connect
        logger.warning(
            f"[TOPIC-FREEFORM] logging failed (non-fatal): {type(e).__name__}: {e}"
        )

# Sentinel-IN: the frontend hardcodes this exact literal on a synthetic
# "here's how that exercise went" turn it sends through the existing POST
# /say path (see astream's `is_exercise_result` branch below) — keep the two
# in sync if this literal ever changes.
EXERCISE_RESULT_PREFIX = "⟦ÜBUNGSERGEBNIS⟧"


class ExerciseMarkerFilter:
    """Streaming character-level filter that withholds Clara's trailing
    exercise marker from a teacher-lesson token stream, however the
    tokenizer happens to split it. Recognizes THREE markers sharing the
    ``[[ÜBUNG`` stem (see ``_EXERCISE_MARKER_PREFIXES``): the original
    ``[[ÜBUNG: <id>]]`` deal, plus ``[[ÜBUNGSWUNSCH: <text>]]`` (demand-only)
    and ``[[ÜBUNG-NEU: <text>]]`` (dev-only live-forge deal) added in
    CLARA-15 P3.

    Feed raw token text to :meth:`feed`; it returns the text (possibly
    empty) that is safe to release right now — append/yield that, never the
    raw token. Call :meth:`finalize` once the stream ends.

    While a run of characters is still a valid PREFIX of one or more of the
    three full marker openers, it is held back (not released) — the set of
    still-possible kinds narrows as each character arrives, since the three
    prefixes diverge at a fixed point right after the shared ``[[ÜBUNG``
    stem and are never a prefix of one another past that point. Once one
    opener fully matches, the filter commits to that kind and swallows:
    everything from that point to the end of the stream is withheld
    permanently (this is the "tolerate a marker outside the very end of a
    reply" rule — the contract asks the model to put it only at the true
    end, but if it doesn't, silently dropping the tail is safer than trying
    to resume mid-reply parsing). ``marker_id`` (the payload — free text for
    ``wunsch``/``neu``, a slug for ``deal``) and ``marker_kind`` are set
    together once a closing ``"]]"`` is seen; a marker that starts but never
    closes before the stream ends leaves both ``None`` and nothing is
    pushed.

    A candidate that turns out NOT to be any marker (an early mismatch, or
    running out of stream mid-candidate) is released as ordinary text —
    either immediately (mismatch) or via :meth:`finalize` (stream ended
    while still a valid partial prefix, e.g. a lone stray "[[" or a cut-off
    "[[ÜBUNGSW").
    """

    _PREFIXES = _EXERCISE_MARKER_PREFIXES
    _CLOSE = EXERCISE_MARKER_CLOSE

    def __init__(self) -> None:
        self._held = ""
        self._swallowing = False  # True once one prefix has fully matched
        self._marker_closed = False
        self._kind: str | None = None  # which prefix matched, once swallowing
        self.marker_id: str | None = None
        self.marker_kind: str | None = None  # "deal" | "wunsch" | "neu"

    def feed(self, token: str) -> str:
        """Consume one streamed token; return the text now safe to release."""
        return "".join(self._feed_char(ch) for ch in token)

    def _feed_char(self, ch: str) -> str:
        if self._swallowing:
            if self._marker_closed:
                return ""  # everything after a confirmed marker is dropped
            self._held += ch
            if self._held.endswith(self._CLOSE):
                prefix = self._PREFIXES[self._kind]
                inner = self._held[len(prefix): -len(self._CLOSE)].strip()
                self.marker_id = inner
                self.marker_kind = self._kind
                self._marker_closed = True
                self._held = ""
            return ""

        candidate = self._held + ch
        matches = [k for k, p in self._PREFIXES.items() if p.startswith(candidate)]
        if matches:
            self._held = candidate
            # By construction the three prefixes diverge before any of them
            # is a prefix of another, so at most one can equal `candidate`.
            exact = next((k for k in matches if self._PREFIXES[k] == candidate), None)
            if exact is not None:
                self._kind = exact
                self._swallowing = True
            return ""

        # Mismatch: `self._held` was never going to become any marker —
        # release it, then check whether `ch` alone starts a fresh candidate
        # (covers e.g. a stray "x[[[ÜBUNG: ..." where the real marker starts
        # one character later).
        flushed, self._held = self._held, ""
        if any(p.startswith(ch) for p in self._PREFIXES.values()):
            self._held = ch
            return flushed
        return flushed + ch

    def finalize(self) -> str:
        """Call once the token stream ends. Returns text that was held but
        never resolved into a confirmed marker start — a stray "[[" (or a
        cut-off partial opener like "[[ÜBUNGSW") that ran out of stream
        instead of diverging mid-way. A confirmed-but-unclosed marker (the
        model got cut off mid-marker) is dropped, not flushed — see the
        class docstring."""
        if self._swallowing:
            self._held = ""
            return ""
        flushed, self._held = self._held, ""
        return flushed


class SentinelStripper:
    """AGENT-007: streaming character-level filter that drops any occurrence
    of ``EXERCISE_RESULT_PREFIX`` (``⟦ÜBUNGSERGEBNIS⟧``) from a teacher-lesson
    token stream, however the tokenizer happens to split it — same
    prefix-holdback technique as ``ExerciseMarkerFilter`` above, simplified
    for a single literal.

    Unlike ``ExerciseMarkerFilter``, a confirmed match does NOT swallow the
    rest of the stream: only the sentinel substring itself is dropped, and
    text before/after it keeps flowing normally, since a fabricated verdict
    the model produces on its own can have real conversational text on
    either side of the hallucinated sentinel (the AGENT-007 tester saw the
    raw sentinel spoken mid-reply, not as the whole reply). Multiple
    occurrences in one reply are all stripped; ``hit`` records whether at
    least one was found, for the warning log + Langfuse span attribute in
    ``ClientWrapper.astream``.

    The REAL ``EXERCISE_RESULT_PREFIX`` turn — the frontend's own synthetic
    report POSTed via ``/say`` — arrives as INPUT text (``astream``'s
    ``text``/``is_exercise_result``), never through this filter, which only
    ever sees the model's OUTPUT stream. So this can only ever fire on a
    turn Clara fabricated herself.
    """

    _TARGET = EXERCISE_RESULT_PREFIX

    def __init__(self) -> None:
        self._held = ""
        self.hit = False

    def feed(self, token: str) -> str:
        """Consume one streamed token; return the text now safe to release."""
        return "".join(self._feed_char(ch) for ch in token)

    def _feed_char(self, ch: str) -> str:
        candidate = self._held + ch
        if self._TARGET.startswith(candidate):
            self._held = candidate
            if candidate == self._TARGET:
                self._held = ""
                self.hit = True
            return ""
        # Mismatch: `self._held` was never going to become the sentinel —
        # release it, then check whether `ch` alone starts a fresh candidate
        # (mirrors ExerciseMarkerFilter's own mismatch-recovery logic).
        flushed, self._held = self._held, ""
        if self._TARGET.startswith(ch):
            self._held = ch
            return flushed
        return flushed + ch

    def finalize(self) -> str:
        """Call once the token stream ends. Returns text that was held but
        never resolved into a confirmed sentinel — a partial match (e.g. a
        cut-off "⟦ÜBUNGSERG") that ran out of stream instead of diverging
        mid-way. Always ordinary text (a stream can't "confirm" a match
        without releasing it in ``_feed_char`` first), so it's always safe
        to flush as-is."""
        flushed, self._held = self._held, ""
        return flushed


class ClientWrapper:
    model = CONVERSATIONAL_MODEL

    def __init__(self, user_id, session_id, voice="happy_harry", lesson_id="lesson_zero",
                 topic="", grammar_focus=None, session_notes=None, vocab_words=None,
                 exercise_catalog=None,
                 max_exchanges_override: int | None = None, trace_session_id: str | None = None,
                 student_name: str | None = None, student_level: str | None = None,
                 forge_enabled: bool = False, picked_pattern: str | None = None):
        self.user_id = user_id
        # CLARA-15 P3: teacher-only, set by pipeline/factory.py from the
        # user's role == "developer" (already-loaded row, no extra query).
        # False for every other lesson type. Read in `astream`'s finally
        # block to decide whether a confirmed `[[ÜBUNG-NEU: ...]]` marker is
        # allowed to push an `exercise_forge` RTVI message, and mirrored
        # onto `self.context.forge_enabled` below for the prompt layer.
        self.forge_enabled = forge_enabled
        self.session_id = session_id
        # Langfuse-only, surface-prefixed form of `session_id` (e.g.
        # "tandem-<hex>") — see pipeline/factory.py. Falls back to the bare
        # id for any caller that doesn't pass it, so a missing kwarg degrades
        # to the pre-existing (unprefixed) behavior rather than an empty
        # attribute.
        self.trace_session_id = trace_session_id or session_id
        # Per-connection parent context for the `llm` span below, set by
        # `PipelineLatencyObserver` on turn open/close (orphan-trace audit,
        # 2026-08-21). Takes
        # priority over pipecat's process-wide `TurnContextProvider` — see
        # `astream`.
        self._turn_context = None
        # OBS-010: set once the first llm span has stamped the rendered
        # system prompt (see astream's finally block).
        self._system_prompt_stamped = False
        # AGENT-00X: loaded ahead of agent_assembly (below) so the lesson's
        # `type` is known before the agent is built — teacher sessions pass a
        # lower reasoning_effort (see the comment on that call) that no other
        # lesson type receives.
        lesson = load_prompts(lesson_id)
        # AGENT-00X: gates the exercise-marker hold-back below. Read from the
        # loaded lesson's own `type`, not `lesson_id == "teacher"` — there is
        # only one teacher lesson today, but the marker contract belongs to
        # the `type: teacher` middleware branch, not to one specific id.
        self._is_teacher_lesson = lesson.get("type") == "teacher"
        # TAND-014c: gates `_augment_tandem_last_turn` below — same
        # data-driven reasoning as `_is_teacher_lesson` just above, so
        # `conversation`/`respond` lessons are never touched by it.
        self._is_tandem_lesson = lesson.get("type") == "tandem"
        # CLARA-20: teacher-only, mirrors self.context.picked_pattern below
        # (same reason self.forge_enabled is mirrored next to
        # self.context.forge_enabled) — kept directly on the wrapper for
        # `_augment_first_result`'s bank lookup, which runs in `astream`
        # and has no reason to go through Context for it.
        self.picked_pattern = picked_pattern
        # CLARA-20: True once the session's first ⟦ÜBUNGSERGEBNIS⟧ turn has
        # been seen — gates the one-time native_note stage-direction
        # injection in `_augment_first_result`. Set on that turn regardless
        # of whether a note actually exists ("first result" is about turn
        # order, not content), so a later result turn is never re-augmented.
        self._first_result_seen: bool = False
        # AGENT-007: True whenever a `deal` exercise marker has been
        # confirmed and pushed to the client and no ⟦ÜBUNGSERGEBNIS⟧ turn has
        # reported how it went yet — set True in the `deal` branch of
        # `astream`'s finally block (where the marker is confirmed), cleared
        # by `_augment_open_exercise` below the moment a real result turn
        # arrives. Teacher-only in practice (nothing else ever deals an
        # exercise), but left unconditional here like every other bool on
        # this class.
        self._exercise_open: bool = False
        self.agent = agent_assembly(
            user_id,
            # gpt-oss-120b's hidden reasoning tokens run ~700-1100 per reply
            # at the provider default ("medium") even for a ~60-word answer
            # (measured via Langfuse) — pure latency with nothing to show for
            # it in a short explanation. Teacher-only: tandem and conversation
            # keep the default, since their prompts are sim-calibrated at that
            # setting (see sim/PROMPT_LOG.md) and this is exactly the kind of
            # change that could shift measured behavior.
            reasoning_effort="low" if self._is_teacher_lesson else None,
        )
        self.context = Context(
            lesson_id=lesson_id,
            agent_voice=voice,
            profile=load_profile(user_id),
            # Grammatik-Tandem layers (TANDEM-001) — non-empty only for the
            # tandem lesson, fetched by the factory at connect and read by the
            # `tandem` middleware branch. Harmless defaults for every other mode.
            topic=topic,
            grammar_focus=grammar_focus or [],
            session_notes=session_notes or [],
            vocab_words=vocab_words or [],
            # AGENT-001 v8: teacher-only greet-by-name (see Context.student_name).
            student_name=student_name,
            # LEVEL round: self-declared CEFR bucket, tandem and teacher both (see Context.student_level).
            student_level=student_level,
            # CLARA-15 P3: mirrors self.forge_enabled above — read by the
            # teacher branch of conversational_prompt.py's prompt assembly.
            forge_enabled=forge_enabled,
            # CLARA-16: teacher-only full exercise catalog (see
            # Context.exercise_catalog) — fetched by the factory at connect
            # and read by the teacher branch of conversational_prompt.py.
            exercise_catalog=exercise_catalog or [],
            # CLARA-20: teacher-only picked-topic pattern id (see
            # Context.picked_pattern) — already validated against
            # `load_taxonomy()` by the factory, threaded through unchanged.
            picked_pattern=picked_pattern,
        )
        self._pipeline_task = None  # Set by factory after pipeline creation
        self.rtvi_processor = None  # Set by factory; used to push bot output to the client
        self._end_task = None
        # End-of-call is detected mid-LLM-stream but DEFERRED until the bot
        # finishes speaking. Otherwise stop_when_done() races: the EndFrame
        # reaches the TurnTrackingObserver and ends the turn before TTS gets
        # the bot's last TextFrame, which orphans the final TTS span (no
        # turn context → falls back to service-level parent → separate trace).
        self._end_pending: bool = False

        # TAND-012: per-session exchange cap (5/10/15), whitelisted in main.py
        # and re-gated to tandem-only in pipeline/factory.py. This wrapper
        # re-loads the YAML itself (the factory's `lesson_snapshot` mutation for
        # the DB row doesn't reach here), so the override is passed explicitly
        # rather than read back off the snapshot.
        self._max_exchanges = max_exchanges_override or lesson["max_exchanges"]
        # Rendered into the tandem wrap-up line (agents/conversational_prompt.py)
        # so the persona's own "wrap up around N exchanges" guidance tracks the
        # actual per-session cap instead of a hardcoded number in the YAML.
        self.context.max_exchanges = self._max_exchanges
        self._exchange_count = 0
        # Goodbye detection arms at the lesson's optional `goodbye_after` (an
        # exchange number, 1-based), or by default only in the final exchange
        # (count cap minus one, floored at 1 for single-turn lessons). The
        # late default prevents the persona's OPENING line — e.g. "great to
        # see you!" matching the "see you" goodbye phrase — from ending the
        # call on turn 1, and scales per lesson: a1_l1 (max 1) armed from the
        # start, b1_l1 (max 5) from exchange 4. Lessons where the partner may
        # genuinely end the chat mid-session set an early explicit value:
        # teacher uses goodbye_after: 5. Tandem carries none today — both
        # YAMLs are back at max_exchanges: 14 with no goodbye_after after the
        # v1 regression, where TAND-004's cap raise to 30 had once silently
        # moved the default arming to exchange 29 and disabled goodbye-driven
        # endings at realistic session lengths; any future cap raise re-arms
        # that same trap.
        self._goodbye_after = lesson.get("goodbye_after") or max(1, self._max_exchanges - 1)

        # TAND-014c: True once `_augment_tandem_last_turn` has fired —
        # guarantees the stage direction is injected at most once per
        # session, mirroring `_first_result_seen`'s one-shot contract above.
        self._last_turn_augmented: bool = False

        # AGENT-001: optional per-lesson opening line. When present,
        # `pipeline/factory.py` injects this text as a synthetic first user
        # turn right after connect — the same LLMContextFrame mechanism
        # `/say` uses — so the agent speaks first instead of the learner
        # facing silence until they break the ice. Absent (None) on every
        # lesson that doesn't set a `kickoff:` key in its YAML, which today
        # is all of them but the teacher (Clara opens with a greeting rather
        # than waiting). Data-driven by design: no lesson_id special-casing
        # here or in the factory.
        self._kickoff = (lesson.get("kickoff") or "").strip() or None

        # REL-002: set by request_wrap_up() (below) the moment a SIGTERM
        # drain injects a wrap-up turn into this session — the exact text
        # returned by that call, so `astream` can recognize it on whatever
        # turn actually carries it and keep it out of the stored transcript
        # / BUG-002 audio<->text pairing, same treatment as the kickoff
        # sentinel above. None for the life of a session that never drains.
        self._wrap_up_sentinel: str | None = None

        # Bot reply is buffered here at the end of each LLM stream. The push to
        # the client happens later, when TTSDurationTracker fires its on_turn_complete
        # callback (`flush_bot_output`) with the authoritative audio duration. That
        # way the message arrival on the client lines up with the server-side end of
        # audio, and the frontend can schedule the bubble reveal precisely.
        self._pending_bot_text: str | None = None

        # Full per-turn transcript captured in-memory as (role, text) tuples.
        # Consumed on disconnect by the post-session evaluator (EVAL-001) so it
        # has the full conversation to judge against the lesson's pass_criterion.
        self._transcript: list[tuple[str, str]] = []

        # Per-turn audio + text are captured separately and paired at
        # disconnect via this shared counter. Drift here corrupts every
        # downstream pronunciation score — see LEARNINGS.md 2026-05-26.
        # Counter is ticked by PipelineLatencyObserver on every
        # UserStoppedSpeakingFrame; audio stamps in `append_user_turn_audio`
        # (called from factory.py), text stamps in `astream`'s finally below.
        self._current_vad_seq: int = 0
        self._user_turn_audio: list[tuple[int, bytes, int]] = []
        self._user_turn_text: list[tuple[int, str]] = []
        self._dropped_audio_count: int = 0

    def request_wrap_up(self) -> str:
        """REL-002: force this session to end after exactly one more
        exchange, and return the stage-direction text for the caller
        (``pipeline/factory.py``'s ``_wrap_up_turn``, invoked from a
        SIGTERM drain) to inject as that exchange's synthetic user turn —
        same ``LLMContext``/``LLMContextFrame`` mechanism ``/say`` and
        ``_kickoff_turn`` use.

        Setting ``_max_exchanges`` to ``_exchange_count + 1`` forces
        ``astream``'s own ``_exchange_count >= _max_exchanges`` branch to
        fire once this reply's tokens stream — regardless of
        ``_goodbye_after`` arming or whether the model's own reply happens
        to contain a GOODBYE_PHRASES match, so the session ends on the very
        next reply no matter what.

        The returned text is also stashed on ``self._wrap_up_sentinel`` so
        ``astream`` can recognize it on whatever turn actually carries it
        and keep it out of ``activity_session.transcript`` and the BUG-002
        audio<->text pairing — the same treatment the kickoff sentinel and
        the exercise-result sentinel already get. Idempotent: calling this
        twice (e.g. a stray double-drain) just re-pins ``_max_exchanges``
        to the same value when ``_exchange_count`` hasn't moved between
        calls, and returns the same text either way.
        """
        self._max_exchanges = self._exchange_count + 1
        text = _WRAP_UP_SENTINEL_EN if self._is_teacher_lesson else _WRAP_UP_SENTINEL_DE
        self._wrap_up_sentinel = text
        return text

    def _augment_first_result(self, text: str) -> str:
        """CLARA-20 fix round: inject the held-back ``native_note`` as a
        stage direction on the session's FIRST ⟦ÜBUNGSERGEBNIS⟧ turn — the
        text sent to the LLM only. The stored transcript and the BUG-002
        audio↔text pairing (both built from ``astream``'s original,
        unaugmented ``text``) never see this — same reasoning as why the
        kickoff sentinel is kept out of both.

        Why turn injection, not prompt memory: gpt-oss-120b reliably drops
        instructions describing a FUTURE turn's behavior — the prompt-only
        version of this (a "hold this note until the first result" line
        sitting in the system prompt all session) never fired in either
        live sim (2026-08-30). An instruction delivered IN the applicable
        turn's own input is followed just as reliably as the kickoff
        sentinel's stage direction is — same channel, different trigger.

        Sets ``_first_result_seen`` the moment a result turn is seen,
        whether or not a note exists for the picked pattern — "first
        result" is about turn order, not content, so a later result turn is
        never re-augmented even when this one had nothing to add. No-op
        outside teacher lessons, on any turn that isn't a result turn, or
        once the first result turn has already passed.
        """
        if not self._is_teacher_lesson or not text.startswith(EXERCISE_RESULT_PREFIX):
            return text
        if self._first_result_seen:
            return text
        self._first_result_seen = True
        entry = load_explanations().get(self.picked_pattern) if self.picked_pattern else None
        native_note = entry.get("native_note") if entry else None
        if not native_note:
            return text
        return text + (
            "\n\n(Stage direction — after reacting to this result, add one "
            "casual by-the-way about real German, one sentence, then move "
            f"on: {native_note})"
        )

    def _augment_tandem_last_turn(self, text: str) -> str:
        """TAND-014c: inject a "this is the last exchange" stage direction
        on the turn whose reply will be the session's FINAL exchange — the
        text sent to the LLM only. Mirrors ``_augment_first_result`` above:
        the stored transcript and the BUG-002 audio↔text pairing (both built
        from ``astream``'s original, unaugmented ``text``) never see this.

        Why turn injection, not prompt memory: same gpt-oss-120b limitation
        documented on ``_augment_first_result`` and on
        ``agents/prompts/teacher.yaml``'s explanation_template comment — it
        reliably drops instructions describing a FUTURE turn's behavior. The
        tandem system prompt already injects a "wrap up around exchange N"
        line (`agents/conversational_prompt.py`'s tandem branch) and the
        model does not reliably comply (TAND-014c) — that line describes a
        future turn. An instruction delivered IN the applicable turn's own
        input is what actually works, same channel as the kickoff sentinel
        and ``_augment_first_result``.

        Off-by-one: this runs BEFORE the ``self._exchange_count += 1`` at
        the top of ``astream``, so ``self._exchange_count`` here still holds
        the number of exchanges ALREADY COMPLETED — the turn about to run
        will produce exchange number ``self._exchange_count + 1``. We want
        that upcoming exchange to be the last one, i.e. equal to
        ``self._max_exchanges`` (5/10/15 via ``max_exchanges_override``, or
        the YAML's 14 otherwise) — so the trigger is
        ``self._exchange_count == self._max_exchanges - 1``.

        Fires at most once per session (``self._last_turn_augmented``), and
        only for `type: tandem` lessons — a no-op for `teacher`,
        `conversation`, and `respond`, and for the kickoff sentinel (tandem
        lessons never set a `kickoff:` key, so ``self._exchange_count`` only
        ever advances on real learner turns here).
        """
        if not self._is_tandem_lesson or self._last_turn_augmented:
            return text
        # REL-002: a drain's wrap-up turn carries its own goodbye direction
        # and bumps _max_exchanges so this trigger would fire on top of it —
        # skip, exactly as the kickoff sentinel is skipped elsewhere.
        if self._wrap_up_sentinel is not None and text == self._wrap_up_sentinel:
            return text
        if self._exchange_count != self._max_exchanges - 1:
            return text
        self._last_turn_augmented = True
        return text + (
            "\n\n(Stage direction — this is the last exchange of today's "
            "tandem session. Briefly answer or react to what your partner "
            "just said, then say a warm goodbye in German and close the "
            "conversation — ask no new question of your own.)"
        )

    def _augment_open_exercise(self, text: str) -> str:
        """AGENT-007: the actual fix for the fabricated-verdict bug — Clara
        can answer a plain acknowledgment ("Okay, I am ready for it.") after
        dealing an exercise as if a graded attempt had already come back: an
        invented sentence, an invented answer, a verdict, sometimes even the
        raw ``EXERCISE_RESULT_PREFIX`` sentinel spoken aloud (see
        ``SentinelStripper`` in ``astream`` for the belt-and-suspenders that
        catches that last case even if this miss). This method is the
        primary defense: while an exercise has been dealt
        (``self._exercise_open``, set True the moment ``ExerciseMarkerFilter``
        confirms a ``deal`` marker — see the `deal` branch in ``astream``'s
        finally block) and no ``EXERCISE_RESULT_PREFIX`` turn has arrived to
        report how it went, EVERY plain learner turn gets a same-turn stage
        direction telling the model the exercise is still open and nothing
        has been graded yet — not just the first one, since the learner may
        chat twice before the app's real result lands.

        Same turn-injection reasoning as ``_augment_first_result``/
        ``_augment_tandem_last_turn`` above: gpt-oss-120b reliably drops
        instructions describing a FUTURE turn's behavior, so this has to
        ride on the applicable turn's own input, not system-prompt memory —
        a `teacher.yaml` trap pair was deliberately NOT chosen for this fix
        (AGENT-007's Proposal-2, prompt bulk trade-off).

        Clears ``self._exercise_open`` the instant a genuine
        ``EXERCISE_RESULT_PREFIX`` turn arrives — from then on a plain turn
        is just a plain turn again, until the next `deal` marker re-opens
        it. No augmentation is added on a result turn itself; that one is
        ``_augment_first_result``'s own turn to augment, not this method's —
        this only does the clearing bookkeeping for it. No-op outside
        teacher lessons, on the kickoff sentinel (defensive only — an
        exercise can't have been dealt yet on exchange 1), or whenever
        ``_exercise_open`` is already False.
        """
        if not self._is_teacher_lesson:
            return text
        if text.startswith(EXERCISE_RESULT_PREFIX):
            self._exercise_open = False
            return text
        if self._kickoff is not None and text == self._kickoff:
            return text
        # REL-002: the drain's wrap-up direction says "end now, no new
        # question"; "remind them you're waiting for the answer" would
        # contradict it on the same turn — the session is ending anyway.
        if self._wrap_up_sentinel is not None and text == self._wrap_up_sentinel:
            return text
        if not self._exercise_open:
            return text
        logger.info(
            f"[AGENT-007] Exercise still open — appending a stage direction "
            f"to session {self.session_id}'s plain turn (exchange "
            f"{self._exchange_count + 1})"
        )
        return text + (
            "\n\n(Stage direction — the exercise you just dealt is still "
            "open; no answer has come back yet, nothing has been graded. "
            "Respond naturally to what they just said — do not describe, "
            "grade, or invent an attempt or a result. If it fits, you may "
            "gently remind them you're waiting for their answer.)"
        )

    async def astream(self, input_dict, config=None):
        """Translates Pipecat format to agent format and streams tokens.

        Owns the per-turn LLM OTel span. Pipecat's built-in TurnTraceObserver
        (enabled via ``PipelineTask(enable_tracing=True)``) opens a turn span
        on each spoken turn and registers it with ``TurnContextProvider``; we
        look it up here and start a child span so the LLM call nests under
        the turn alongside the auto-emitted STT and TTS spans.

        Attributes emitted follow OTel ``gen_ai.*`` semantic conventions so
        Langfuse's cost engine can read ``input_tokens`` / ``output_tokens``
        directly; ``input`` / ``output`` carry the user message and full
        reply for evaluation; the custom block (voice/lesson_id/exchange)
        preserves the per-turn metadata we used to put on the manual
        Langfuse Generation.

        ``LangchainProcessor`` itself is uninstrumented (it's a
        ``FrameProcessor``, not an ``LLMService``), so this is the only LLM
        span on the trace.

        AGENT-00X, teacher lessons only: tokens are routed through an
        ``ExerciseMarkerFilter`` so a trailing exercise marker — the
        original ``[[ÜBUNG: <id>]]`` deal, or (CLARA-15 P3)
        ``[[ÜBUNGSWUNSCH: <text>]]``/``[[ÜBUNG-NEU: <text>]]`` — never
        reaches ``full_response`` (and therefore never reaches TTS, the
        stored transcript, or the pending bot text — all three are built
        from ``full_response``). The confirmed payload, if any, is handled
        after the loop by kind — see the `finally` block below.

        CLARA-20 fix round: ``text`` (the original input) drives the
        transcript/VAD-pairing bookkeeping below; ``llm_text`` — ``text``
        plus ``_augment_first_result``'s stage direction, when it applies —
        is what actually reaches the model. See that method's docstring for
        why. TAND-014c: ``_augment_tandem_last_turn`` chains onto the same
        ``llm_text``, on tandem sessions only, and (like
        ``_augment_first_result``) must run BEFORE the ``_exchange_count``
        increment below — its own docstring works out why. AGENT-007:
        ``_augment_open_exercise`` chains onto ``llm_text`` last, teacher
        lessons only — same before-the-increment requirement, same reasoning.

        AGENT-007, teacher lessons only, detected in-stream (post-yield code
        is unreliable here, same reason the end-of-call trigger below is
        detected in-stream rather than after): a ``SentinelStripper`` runs
        alongside ``ExerciseMarkerFilter`` on every streamed token so a
        fabricated ``EXERCISE_RESULT_PREFIX`` sentinel — Clara hallucinating
        her own graded-result turn off a plain acknowledgment — is never
        spoken, stored, or shown, even though ``_augment_open_exercise``
        above is the primary defense against the fabrication itself. See the
        `finally` block below for the warning log + Langfuse span attribute
        this leaves behind when it fires.

        PERF-005: the token loop below is wrapped in an up-to-2-attempt loop
        bounding only the wait for the FIRST token of each attempt
        (``_first_token_bounded``, ``CONVERSATION_FIRST_TOKEN_S`` seconds,
        default 8 — p90 healthy TTFT is ~1.48s). Attempt 2, if reached, is a
        LangGraph RESUME (``self.agent.astream(None, config=run_config, ...)``),
        not a re-invoke with the same ``messages`` — empirically verified
        with a throwaway create_agent()+InMemorySaver() harness (fake
        streaming model, forced first-token timeout via the same
        wait_for/aclose() pattern used here): re-invoking with the same
        input produced TWO copies of the human message in
        ``agent.get_state(config).values["messages"]`` after the retry
        (LangGraph's ``add_messages`` reducer appends a dict-built message as
        new because it gets a fresh id each call), while resuming with
        ``None`` produced exactly ONE — LangGraph checkpoints the input
        merge as its own step, before the model node runs, so a cancelled
        node has nothing of its own to re-merge on resume. See the retry
        loop below for what happens on a second failure (fallback line,
        span marked ERROR, session stays alive — no `_end_pipeline`).
        """
        text = input_dict.get("input", "")
        llm_text = self._augment_first_result(text)
        llm_text = self._augment_tandem_last_turn(llm_text)
        llm_text = self._augment_open_exercise(llm_text)
        messages = {"messages": [{"role": "user", "content": llm_text}]}

        self._exchange_count += 1

        run_config = {"configurable": {"thread_id": self.user_id}}

        full_response = []
        ttft_ns = None
        final_usage = None
        final_response_metadata = None
        marker_filter = ExerciseMarkerFilter() if self._is_teacher_lesson else None
        # AGENT-007: teacher lessons only — strips a fabricated
        # EXERCISE_RESULT_PREFIX sentinel from Clara's own output stream.
        # See SentinelStripper's docstring and the astream docstring above.
        sentinel_stripper = SentinelStripper() if self._is_teacher_lesson else None

        # Prefer the per-connection context PipelineLatencyObserver hands us
        # on turn open (self._turn_context) over pipecat's process-wide
        # TurnContextProvider singleton — under concurrent clients that
        # singleton can be holding another connection's turn by the time
        # this coroutine resumes. Falls back to the singleton, then to None
        # (no parent → span becomes a root span, harmless), preserving
        # behavior for any caller that never wires a per-connection context.
        turn_ctx = self._turn_context or get_current_turn_context()
        span_start_ns = time.time_ns()

        async def _first_token_bounded(source_agen):
            """PERF-005: bound only the FIRST item pulled from ``source_agen``
            to ``conversation_first_token_s`` — every item after streams
            unbounded (a healthy reply can keep streaming for several
            seconds once it starts; only the stall BEFORE anything arrives
            is the failure mode being bounded here).

            On a first-token timeout, closes ``source_agen`` before
            re-raising so the underlying LangGraph/httpx stream doesn't keep
            running unobserved, and so the caller never touches this
            generator again (a second read from an already-timed-out
            generator is exactly how a stray late token could leak into the
            output — closing it here makes that structurally impossible).
            """
            try:
                first_item = await asyncio.wait_for(
                    source_agen.__anext__(), timeout=conversation_first_token_s
                )
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                await source_agen.aclose()
                raise
            yield first_item
            async for item in source_agen:
                yield item

        with tracer.start_as_current_span(
            "llm",
            context=turn_ctx,
        ) as llm_span:
            llm_span.set_attribute("gen_ai.system", "openrouter")
            llm_span.set_attribute("gen_ai.request.model", CONVERSATIONAL_MODEL)
            llm_span.set_attribute("gen_ai.operation.name", "chat")
            llm_span.set_attribute("gen_ai.output.type", "text")
            # Explicit, not just baggage-inherited: the exporter's orphan
            # filter (agents/observability.py) drops a parentless span with
            # no user.id, and baggage propagation has its own failure modes
            # (a context that never got attached, a task that lost it) — a
            # span this expensive (token usage, cost) must never go anonymous
            # silently for either reason.
            llm_span.set_attribute("user.id", self.user_id)
            llm_span.set_attribute("langfuse.session.id", self.trace_session_id)
            # Observation-level input — shown on the LLM observation row in
            # Langfuse. CLARA-20 fix round: this is `llm_text`, the text
            # actually sent to the model (original `text` plus any
            # `_augment_first_result` stage direction) — deliberately, so a
            # trace shows what the model really saw, which is exactly the
            # kind of thing this fix round needed to debug.
            llm_span.set_attribute("langfuse.observation.input", llm_text)
            # v4 (observations-first): the trace's Input column reads from the
            # ROOT observation — the observer's live turn span — not from the
            # deprecated `langfuse.trace.input` on a child. If no turn span is
            # live (tracing off, or this llm span is its own root), the
            # observation.input above already covers it.
            turn_span = get_current_turn_span()
            if turn_span is not None and turn_span.is_recording():
                turn_span.set_attribute("langfuse.observation.input", llm_text)
            llm_span.set_attribute("voice", self.context.agent_voice)
            llm_span.set_attribute("lesson_id", self.context.lesson_id)
            llm_span.set_attribute("exchange", self._exchange_count)

            try:
                for attempt_num in (1, 2):
                    agen = (
                        self.agent.astream(
                            messages,
                            config=run_config,
                            context=self.context,
                            stream_mode="messages",
                        )
                        if attempt_num == 1
                        # PERF-005 retry: resume from the LangGraph checkpoint
                        # instead of re-invoking with `messages` again — see
                        # the astream docstring above for the empirical
                        # finding (resume leaves exactly one copy of the
                        # human message in thread state; re-invoking
                        # duplicates it).
                        else self.agent.astream(
                            None,
                            config=run_config,
                            context=self.context,
                            stream_mode="messages",
                        )
                    )
                    attempt_start_ns = time.time_ns()
                    try:
                        async for token, _ in _first_token_bounded(agen):
                            if hasattr(token, "content") and token.content:
                                if ttft_ns is None:
                                    ttft_ns = time.time_ns()
                                # Strip markdown asterisks here, once, at the single point
                                # text leaves the LLM stream — every downstream consumer
                                # (TTS via yield, goodbye detection via full_response,
                                # the chat bubble stash, the transcript) reads clean text.
                                # The LLM occasionally emits `**word**`; asterisks have no
                                # legitimate use in spoken output (TAND-002).
                                content = token.content.replace("*", "")
                                # AGENT-00X: teacher lessons only. Text held back as a
                                # candidate/confirmed exercise marker comes back as ""
                                # here — nothing below runs for it this iteration (it
                                # either flushes later as ordinary text via a later
                                # feed()/finalize() call, or is a confirmed marker and
                                # never flushes at all).
                                if marker_filter is not None:
                                    content = marker_filter.feed(content)
                                # AGENT-007: strip a fabricated
                                # EXERCISE_RESULT_PREFIX sentinel before it
                                # can ever reach TTS/transcript/pending bot
                                # text — see SentinelStripper's docstring.
                                # Runs on whatever marker_filter released
                                # this iteration (possibly "" — nothing to
                                # check yet).
                                if sentinel_stripper is not None and content:
                                    content = sentinel_stripper.feed(content)
                                if content:
                                    full_response.append(content)
                                    yield content

                                    # End trigger: count cap reached, OR a goodbye phrase
                                    # appears once armed (final exchange — see
                                    # _goodbye_after). Detected in-stream (post-yield code
                                    # is unreliable) but only MARKED as pending here — the
                                    # actual stop_when_done() call happens in
                                    # flush_bot_output() after BotStoppedSpeakingFrame so
                                    # the final TTS span gets to record under the turn
                                    # before EndFrame races through and closes the turn
                                    # span. See LEARNINGS.md for the race condition that
                                    # motivates this.
                                    if (not self._end_pending
                                            and (self._exchange_count >= self._max_exchanges
                                                 or (self._exchange_count >= self._goodbye_after
                                                     and _contains_goodbye("".join(full_response))))):
                                        reason = (
                                            "max_exchanges" if self._exchange_count >= self._max_exchanges
                                            else "goodbye"
                                        )
                                        logger.info(
                                            f"[END] Pending pipeline close ({reason}, "
                                            f"exchange {self._exchange_count}/{self._max_exchanges}) "
                                            f"— will fire after bot finishes speaking"
                                        )
                                        self._end_pending = True

                            if getattr(token, "usage_metadata", None):
                                final_usage = token.usage_metadata
                            # OBS-008: on the streamed path only the FINAL chunk carries
                            # a populated response_metadata (finish_reason/model_name/
                            # model_provider — see LEARNINGS.md); every prior chunk's is
                            # empty, so "last non-empty wins" naturally lands on it.
                            # OpenRouter's actually-served provider is not reachable
                            # here either (same limitation as the one-shot judge path —
                            # see agents/observability.py::unwrap_structured_output) and
                            # adding an extra HTTP call to fetch it is out of scope.
                            if getattr(token, "response_metadata", None):
                                final_response_metadata = token.response_metadata

                        # AGENT-00X: stream ended — flush any leftover held text that
                        # never resolved into a confirmed marker (e.g. a stray "[["
                        # that ran out of stream). A CONFIRMED marker's text is never
                        # flushed here (finalize() drops it) — it was already fully
                        # withheld above, and its id (if any) is handled below.
                        if marker_filter is not None:
                            tail = marker_filter.finalize()
                            if tail:
                                full_response.append(tail)
                                yield tail
                        # AGENT-007: same flush for a partial-but-unconfirmed
                        # sentinel match (e.g. a stray "⟦ÜBUNGSERG" that ran
                        # out of stream) — always ordinary text, safe to
                        # release as-is (see SentinelStripper.finalize).
                        if sentinel_stripper is not None:
                            tail = sentinel_stripper.finalize()
                            if tail:
                                full_response.append(tail)
                                yield tail
                        break
                    except Exception as e:
                        # Narrow on purpose: asyncio.CancelledError and
                        # GeneratorExit are BaseException, not Exception, so
                        # a real disconnect/cancel during this await is never
                        # caught here — it propagates normally. Only actual
                        # LLM-call failures (timeout or otherwise) land in
                        # this branch.
                        elapsed_s = (time.time_ns() - attempt_start_ns) / 1_000_000_000
                        is_timeout = isinstance(e, asyncio.TimeoutError)
                        # Named `failure_reason`, not `reason` — that name is
                        # already used (different meaning: max_exchanges vs
                        # goodbye) inside the per-token body above, and this
                        # `except` shares the same function scope with it.
                        failure_reason = (
                            "first-token timeout" if is_timeout
                            else f"{type(e).__name__}: {e}"
                        )
                        if full_response:
                            # Content from THIS attempt already streamed (and
                            # was already spoken via TTS) before the failure
                            # hit — retrying would append an unrelated second
                            # reply on top of live audio, and the fallback
                            # line would sound bizarre stitched onto real
                            # partial speech. Stop here: whatever was
                            # captured stands as the (degraded) turn output,
                            # same as any other early-terminated stream, but
                            # the span is marked ERROR so it shows up in
                            # Langfuse instead of silently looking healthy.
                            logger.error(
                                f"[PERF-005] LLM stream failed mid-reply after "
                                f"{elapsed_s:.1f}s ({failure_reason}), exchange "
                                f"{self._exchange_count}/{self._max_exchanges} — "
                                f"keeping the partial reply already streamed, "
                                f"not retrying"
                            )
                            mark_span_error(llm_span, f"mid-stream failure: {failure_reason}")
                            break
                        if attempt_num == 1:
                            logger.warning(
                                f"[PERF-005] LLM produced no first token after "
                                f"{elapsed_s:.1f}s ({failure_reason}) — retrying once, "
                                f"exchange {self._exchange_count}/{self._max_exchanges}"
                            )
                            continue
                        # Attempt 2 (the retry) also produced nothing. The
                        # learner must perceive SOMETHING rather than dead
                        # air again — speak a fixed fallback line (goes
                        # through the normal TTS/bot-output push below, same
                        # as any real reply) instead of a third attempt, mark
                        # the span as an error, and keep the session alive —
                        # no _end_pipeline here even if this exchange happens
                        # to be the one that reaches the cap.
                        fallback_reason = (
                            f"first-token timeout twice ({elapsed_s:.1f}s on retry)"
                            if is_timeout else f"retry failed: {failure_reason}"
                        )
                        logger.error(
                            f"[PERF-005] retry also failed ({fallback_reason}) — "
                            f"speaking the fallback line, exchange "
                            f"{self._exchange_count}/{self._max_exchanges}"
                        )
                        mark_span_error(llm_span, fallback_reason)
                        fallback_text = (
                            _FALLBACK_REPLY_EN if self._is_teacher_lesson
                            else _FALLBACK_REPLY_DE
                        )
                        full_response.append(fallback_text)
                        yield fallback_text
                        break
            finally:
                output_text = "".join(full_response)
                if marker_filter is not None and marker_filter.marker_id is not None:
                    # AGENT-00X: the marker itself never entered `full_response`
                    # (the filter withheld it entirely, above) — this only trims
                    # the trailing whitespace the announcing sentence left
                    # behind once the marker was cut away, per the "strip the
                    # marker and trailing whitespace" contract.
                    output_text = output_text.rstrip()
                # AGENT-007: a fabricated EXERCISE_RESULT_PREFIX sentinel was
                # detected and stripped mid-stream (SentinelStripper above) —
                # the raw text is already safely gone from what was yielded,
                # but this should never happen given a real learner turn (the
                # genuine sentinel only ever arrives IN via the frontend's own
                # /say POST, never out of Clara's mouth), so it's worth a
                # warning + a span attribute to catch on Langfuse.
                if sentinel_stripper is not None and sentinel_stripper.hit:
                    logger.warning(
                        f"[AGENT-007] Stripped a fabricated "
                        f"{EXERCISE_RESULT_PREFIX!r} sentinel from Clara's "
                        f"own reply — session {self.session_id}, exchange "
                        f"{self._exchange_count}/{self._max_exchanges}"
                    )
                    llm_span.set_attribute("teacher.fabricated_result", True)
                llm_span.set_attribute("langfuse.observation.output", output_text)
                # v4 root-observation output — same contract as the input
                # stamp above. The turn span is still recording here: it ends
                # on BotStoppedSpeaking, which is always after LLM streaming.
                turn_span = get_current_turn_span()
                if turn_span is not None and turn_span.is_recording():
                    turn_span.set_attribute("langfuse.observation.output", output_text)
                if final_usage:
                    if (n := final_usage.get("input_tokens")) is not None:
                        llm_span.set_attribute("gen_ai.usage.input_tokens", n)
                    if (n := final_usage.get("output_tokens")) is not None:
                        llm_span.set_attribute("gen_ai.usage.output_tokens", n)
                if final_response_metadata:
                    if (m := final_response_metadata.get("model_name")) is not None:
                        llm_span.set_attribute("gen_ai.response.model", m)
                if ttft_ns is not None:
                    # Keep our existing TTFT metric as a custom attribute. Span
                    # duration itself now covers full LLM streaming (not just
                    # first-token), which matches what gen_ai.* conventions expect.
                    llm_span.set_attribute(
                        "metrics.ttft_ms",
                        (ttft_ns - span_start_ns) / 1_000_000,
                    )

                # OBS-010: the rendered system prompt — ledger layers, vocab
                # words, topic, notes all injected — exists nowhere else once
                # the session logger is gone (the DB lesson_snapshot freezes
                # only the raw YAML), so stamp it once on this session's first
                # llm span. Rendered by the middleware during the stream above,
                # so it's available here; the span is still open.
                if not self._system_prompt_stamped:
                    prompt = get_last_system_prompt()
                    if prompt:
                        llm_span.set_attribute(
                            "langfuse.observation.metadata.system_prompt", prompt
                        )
                        self._system_prompt_stamped = True

                # Stash the full reply; the actual push to the client now happens
                # in `flush_bot_output`, invoked by TTSDurationTracker once TTS has
                # finished streaming audio (server-side end-of-speech). The framework
                # would otherwise dispatch duplicates (AggregatedTextFrame on the LLM
                # side AND TTSTextFrame on the TTS side); we still emit exactly one
                # message per turn, just timed against the audio.
                self._pending_bot_text = output_text.strip() or None

                # Capture the turn for the post-session evaluator. Runs in the
                # finally so even partial turns (interruptions, errors mid-stream)
                # land in the transcript with whatever the bot managed to say.
                #
                # AGENT-001: the kickoff (self._kickoff, if set) is a stage
                # direction WE injected at connect — not something the learner
                # said. Letting it in would put a fake "User:" line at the top
                # of activity_session.transcript and hand it to any evaluator
                # that reads the transcript. The bot's greeting that follows
                # IS real and must stay, so only the bot line is appended for
                # this one turn.
                is_kickoff = self._kickoff is not None and text == self._kickoff
                # AGENT-00X sentinel-IN: a turn the frontend sends with this
                # exact prefix (through the existing POST /say path) is not
                # something the learner said — it's a synthetic report of how
                # a Clara-issued exercise went. It must stay out of the stored
                # transcript and out of the audio↔text pairing below for the
                # same reason the kickoff does (there's no learner speech and
                # no matching audio clip behind it), while the exchange still
                # counts (the increment at the top of astream is unconditional)
                # and the reply streams normally either way.
                is_exercise_result = text.startswith(EXERCISE_RESULT_PREFIX)
                # REL-002: the SIGTERM-drain wrap-up turn (see
                # request_wrap_up above) is a stage direction WE injected,
                # not something the learner said — same reasoning and same
                # treatment as the kickoff sentinel just above.
                is_wrap_up = (
                    self._wrap_up_sentinel is not None and text == self._wrap_up_sentinel
                )
                if not is_kickoff and not is_exercise_result and not is_wrap_up:
                    self._transcript.append(("user", text))
                self._transcript.append(("bot", output_text))
                # Stamp the user text with the current VAD-stop seq so the
                # pronunciation evaluator can pair it with the matching audio
                # at disconnect (BUG-002). Empty/whitespace inputs are skipped
                # — `/say` injection or the (extremely rare) all-whitespace
                # transcript shouldn't reach Azure as a reference text anyway.
                # The kickoff, exercise-result sentinel, and wrap-up sentinel
                # are skipped for the same reason: none has a matching
                # VAD-stop seq / audio clip to pair with (AGENT-001 /
                # AGENT-00X / REL-002).
                if text.strip() and not is_kickoff and not is_exercise_result and not is_wrap_up:
                    self._user_turn_text.append((self._current_vad_seq, text))

                # AGENT-00X / CLARA-15 P3: push the confirmed exercise
                # marker to the client now that the reply is fully assembled
                # and stripped above. Guarded like `flush_bot_output`'s own
                # client push — a stubbed/absent rtvi_processor (unit tests,
                # a connect that never finished wiring the pipeline) must
                # never raise here. Behavior branches by `marker_kind`:
                #   deal   -> unchanged from pre-CLARA-15 (byte-identical
                #             push of {"type": "exercise_request", ...}).
                #   wunsch -> never pushes anything; only logs the demand.
                #   neu    -> always logs the demand too, then pushes
                #             {"type": "exercise_forge", ...} ONLY when this
                #             session is forge_enabled (role == developer);
                #             otherwise treated like wunsch (log-only) plus
                #             one extra warning — defense in depth, since
                #             the prompt itself shouldn't offer this marker
                #             to a non-developer session at all.
                if marker_filter is not None and marker_filter.marker_id is not None:
                    payload = marker_filter.marker_id
                    kind = marker_filter.marker_kind
                    if kind == "deal":
                        # CLARA-16: correct a near-miss id against everything
                        # actually printed on Clara's page (focus + catalog)
                        # before it reaches the client — see _snap_pattern_id.
                        known_ids = {
                            p["pattern_id"]
                            for layer in (
                                self.context.grammar_focus,
                                self.context.exercise_catalog,
                            )
                            for p in layer
                            if isinstance(p, dict) and p.get("pattern_id")
                        }
                        pattern_id = _snap_pattern_id(payload, known_ids)
                        if _EXERCISE_MARKER_ID_RE.match(pattern_id):
                            # AGENT-007: this deal is confirmed and about to
                            # be pushed to the client — open the flag
                            # `_augment_open_exercise` reads on every
                            # subsequent plain turn until the real result
                            # turn arrives (or the NEXT deal re-opens it).
                            self._exercise_open = True
                            logger.info(f"[EXERCISE] Dealt pattern {pattern_id!r} to the client")
                            if self.rtvi_processor is not None and hasattr(
                                self.rtvi_processor, "send_server_message"
                            ):
                                await self.rtvi_processor.send_server_message(
                                    {"type": "exercise_request", "pattern_id": pattern_id}
                                )
                        else:
                            logger.warning(
                                f"[EXERCISE] Dropping malformed marker id {pattern_id!r}"
                            )
                    elif kind in ("wunsch", "neu"):
                        topic = payload.strip()
                        if not (1 <= len(topic) <= 80):
                            logger.warning(
                                f"[EXERCISE] Dropping malformed {kind} payload {payload!r} "
                                f"(must be 1-80 chars after stripping)"
                            )
                        else:
                            await self._log_exercise_demand(topic, kind)
                            if kind == "neu":
                                if self.forge_enabled:
                                    logger.info(
                                        f"[EXERCISE] Forge-dealing topic {topic!r} to the client"
                                    )
                                    if self.rtvi_processor is not None and hasattr(
                                        self.rtvi_processor, "send_server_message"
                                    ):
                                        await self.rtvi_processor.send_server_message(
                                            {"type": "exercise_forge", "topic": topic}
                                        )
                                else:
                                    # Defense in depth: the prompt only ever
                                    # offers `[[ÜBUNG-NEU: ...]]` when
                                    # forge_enabled is True, so reaching here
                                    # means the model produced it anyway —
                                    # log loudly, but treat exactly like a
                                    # `wunsch` (demand already logged above,
                                    # no push).
                                    logger.warning(
                                        f"[EXERCISE] Dropping ÜBUNG-NEU push — "
                                        f"forge_enabled is False for this session "
                                        f"(topic={topic!r})"
                                    )

    async def _log_exercise_demand(self, topic: str, kind: str) -> None:
        """CLARA-15 P3: log a demand signal for a confirmed
        ``[[ÜBUNGSWUNSCH: ...]]``/``[[ÜBUNG-NEU: ...]]`` marker — a topic
        Clara had no printed exercise for. This is the measurement the
        feature exists for; it is otherwise invisible to the learner.

        One Langfuse span named ``teacher-exercise-demand``, attached to
        this session's LIVE trace the same way the ``llm`` span above joins
        it (same ``turn_ctx``, same explicit ``user.id``/
        ``langfuse.session.id`` stamping — mirrors that span's own
        construction). DEFAULT Langfuse environment — never
        ``langfuse.environment: "forge"`` (that env is reserved for
        background content-forge volume; this is learner-facing). Also
        emits one loguru INFO line with the same fields. Wrapped end-to-end:
        a logging failure here must never break the stream — the session
        goes on regardless.
        """
        try:
            turn_ctx = self._turn_context or get_current_turn_context()
            with tracer.start_as_current_span(
                "teacher-exercise-demand", context=turn_ctx
            ) as span:
                span.set_attribute("user.id", self.user_id)
                span.set_attribute("langfuse.session.id", self.trace_session_id)
                span.set_attribute("langfuse.observation.input", topic)
                span.set_attribute("langfuse.observation.metadata.topic", topic)
                span.set_attribute("langfuse.observation.metadata.kind", kind)
                span.set_attribute(
                    "langfuse.observation.metadata.forge_enabled", self.forge_enabled
                )
            logger.info(
                f"[DEMAND] topic={topic!r} kind={kind!r} forge_enabled={self.forge_enabled}"
            )
        except Exception as e:  # noqa: BLE001 — demand logging must never break the stream
            logger.warning(
                f"[DEMAND] logging failed (non-fatal): {type(e).__name__}: {e}"
            )

    def render_transcript(self) -> str:
        """Format the captured turns as a single string for the evaluator prompt."""
        return "\n\n".join(f"{role.capitalize()}: {body}" for role, body in self._transcript)

    def bot_character_count(self) -> int:
        """Total characters across this session's bot turns (AUDIO-COST-001).

        Approximates billed MiniMax TTS characters: `_transcript`'s "bot"
        entries are `astream`'s `output_text` — the same text handed to TTS
        via the yielded stream, minus the AGENT-00X exercise-marker
        stripping in teacher sessions (the marker itself is never spoken,
        so excluding it from the count is correct, not an approximation
        error). Used by `pipeline/factory.py`'s post-session-analysis cost
        stamp.
        """
        return sum(len(body) for role, body in self._transcript if role == "bot")

    def set_turn_context(self, context) -> None:
        """Set/clear this connection's live turn span context. Called by
        ``PipelineLatencyObserver`` on turn open (the new turn's context) and
        close (``None``) — see that class's ``_open_turn``/``_close_turn``.
        Per-connection, unlike pipecat's process-wide ``TurnContextProvider``:
        under concurrent clients that singleton can hand a turn from another
        connection as parent, this cannot.
        """
        self._turn_context = context

    def vad_stop(self) -> None:
        """Tick the VAD-stop sequence number. Called by
        ``PipelineLatencyObserver`` on every ``UserStoppedSpeakingFrame``.
        The counter is the shared identifier that pairs audio (stamped in
        ``append_user_turn_audio``) with text (stamped in ``astream``'s
        finally block) at disconnect — see LEARNINGS.md 2026-05-26 (BUG-002).
        """
        self._current_vad_seq += 1

    def append_user_turn_audio(self, audio: bytes, sample_rate: int) -> None:
        """Buffer one user turn's audio with the current VAD-stop seq.
        Called by the audiobuffer's ``on_user_turn_audio_data`` event handler
        in ``pipeline/factory.py``."""
        self._user_turn_audio.append((self._current_vad_seq, audio, sample_rate))

    def has_user_turn_audio(self) -> bool:
        return bool(self._user_turn_audio)

    def iter_user_turn_audio(self):
        """Yield ``(text, audio_bytes, sample_rate)`` for each user turn that
        has both a captured audio clip and a non-empty transcript.

        Pairs via the shared VAD-stop seq stamped on both sides during the
        session. Orphan audio (silent VAD trigger, breath, mic glitch, or an
        STT miss where Deepgram produced no transcript) is dropped silently
        and counted in ``_dropped_audio_count`` for the session log.
        """
        text_by_seq = {seq: t for seq, t in self._user_turn_text if t.strip()}
        for seq, audio, sr in self._user_turn_audio:
            if seq in text_by_seq:
                yield text_by_seq[seq], audio, sr
            else:
                self._dropped_audio_count += 1
        if self._dropped_audio_count:
            logger.info(
                f"Dropped {self._dropped_audio_count} audio chunk(s) with no "
                f"matching transcript (silent VAD / STT miss)"
            )

    async def flush_bot_output(self, audio_duration_ms: float) -> None:
        """Push the buffered bot reply to the RTVI client with the turn's audio duration.

        Called by ``TTSDurationTracker.on_turn_complete`` after the last TTS audio
        frame has gone out. ``audio_duration_ms`` is the authoritative server-side
        TTS audio length; the frontend uses it (plus the ``botStartedSpeaking``
        timestamp it captures on receive) to reveal the bubble after playback
        finishes in the browser.

        CLARA-18: the bot-output push and the end-of-call block below used to
        share one early-return guard (``if not text or rtvi_processor is
        None: return``), which meant a final turn with no flushable text —
        e.g. a marker-only reply the ``ExerciseMarkerFilter`` withheld in
        full — returned before ever reaching the end block, stranding
        ``_end_pending`` forever and hanging the session open exactly like
        the bug this fixes. The two are now independent: the bot-output push
        is skipped when there's nothing to send, but the end block always
        runs when armed, regardless of whether a bot-output message went out.
        """
        text = self._pending_bot_text
        self._pending_bot_text = None
        if text and self.rtvi_processor is not None:
            msg = _BotOutputMessageWithDuration(
                data=_BotOutputDataWithDuration(
                    text=text,
                    spoken=True,
                    aggregated_by="turn",
                    audio_duration_ms=audio_duration_ms,
                )
            )
            await self.rtvi_processor.push_transport_message(msg)

        # If end-of-call was detected mid-LLM-stream, NOW is the safe moment
        # to fire stop_when_done(): the bot has finished speaking, the final
        # TTS span is closed under the turn, and the EndFrame can race through
        # the observers without orphaning any in-flight span.
        if self._end_pending and self._end_task is None and self._pipeline_task:
            # CLARA-18: tell the client NOW, over the still-open socket, that
            # this session is closing. The WS close frame that follows
            # stop_when_done() below is not reliably delivered through
            # Railway's edge (a half-open socket — BUG-009's pathology, in
            # the server->client direction this time), and a client that
            # misses it sits in a dead room 404-ing at /say. Guarded: a push
            # failure here must never block the close itself.
            if self.rtvi_processor is not None and hasattr(
                self.rtvi_processor, "send_server_message"
            ):
                try:
                    await self.rtvi_processor.send_server_message(
                        {"type": "session_ending", "reason": "agent"}
                    )
                    logger.info("[END] Pushed session_ending to the client")
                except Exception as e:
                    logger.warning(
                        f"[END] session_ending push failed (non-fatal): "
                        f"{type(e).__name__}: {e}"
                    )
            logger.info("[END] Bot finished speaking — closing pipeline")
            self._end_task = asyncio.create_task(self._end_pipeline())
            # BUG-006: nothing awaits this task — without a done-callback an
            # exception in _end_pipeline vanishes and the session hangs open
            # with no log line.
            self._end_task.add_done_callback(
                lambda t: t.cancelled()
                or t.exception() is None
                or logger.error(f"_end_pipeline failed: {t.exception()!r}")
            )

    async def _end_pipeline(self):
        """Gracefully end the pipeline once in-flight TTS audio is done streaming.

        ``stop_when_done()`` queues an ``EndFrame`` at the pipeline source. The
        frame travels downstream behind whatever is already in transit (LLM
        text frames, TTS audio frames), so the bot's final reply finishes
        playing before the pipeline shuts down. The earlier ``queue_frame
        (CancelTaskFrame())`` approach didn't work — that path injects the
        frame *downstream* from the source, but ``CancelTaskFrame`` only
        triggers cancellation when it reaches ``PipelineTask._source_push_frame``
        from *upstream*, so it just slid through inert.
        """
        if self._pipeline_task:
            await self._pipeline_task.stop_when_done()
