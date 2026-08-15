"""Deterministic guards at the grammar-ledger write boundary (LEDGER-001).

Two writers turn an LLM's classified grammar slips into ``user_errors``
rows — the tandem debrief (``agents/debrief.py``) and the situation
transcript / Briefkasten letter harvest (``agents/error_extractor.py``). A
2026-08-15 trace review found three shapes of false row reaching the ledger
from those judges:

1. Standard spoken-German schwa-drop filed as a real ``subjekt-verb-endung``
   break ("ich komm" / "ich mach" → flagged, three sessions, same
   non-error — the recognizer's spelling of ordinary speech, not the
   learner's mistake, same argument as SATZ-022).
2. A das/dass homophone filed as a grammar mistake, for the same reason.
3. A harvested quote that is not actually a substring of what the learner
   wrote or said — a misquote (BRIEF-003's class: "also" written down as
   "auch").
4. A "corrected" sentence that doesn't correct anything: empty, identical
   to the evidence, or the flagged wrong text still sitting there unchanged.

None of these need an LLM verify call (that's LEDGER-001 Proposal-2 —
grammatical/meaning-preserving verification of the correction — explicitly
out of scope here). All four are decidable from the evidence/correction
text alone, so they are caught here, deterministically, before either
writer's ``record_grammar_error`` call. Both ``debrief()`` and
``extract_errors()`` run every kept row through ``ledger_guard_reason``
right before they return — nothing sits between that filter and the actual
DB write in any current caller (``pipeline/factory.py``,
``briefkasten/routes.py``, ``szenario/routes.py``), so filtering here is
filtering at the ledger boundary.

Kept in ``grammar/`` (not ``agents/``) for the same reason the taxonomy
loader lives here: both writers already import from this package, and
nothing here needs the Pipecat/LangChain stack ``agents/__init__.py``
drags in.
"""

import re

# Trailing/leading punctuation a token can carry without changing its
# identity for a word-level diff — quotes, sentence enders, brackets.
_TOKEN_PUNCT = ",.!?;:\"'„“”‚’`()…"
_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_PUNCT_RE = re.compile(r"""[,.!?;:"'„“”‚’`()…\-]""")


def _normalize(text: str, *, strip_punctuation: bool = False) -> str:
    """Whitespace-collapse + lowercase for a whole quote/sentence; optionally
    also drop punctuation entirely (the debrief judges a spoken transcript,
    where punctuation is Deepgram's guess, not the learner's)."""
    text = text or ""
    if strip_punctuation:
        text = _SENTENCE_PUNCT_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _strip_token(token: str) -> str:
    return token.strip(_TOKEN_PUNCT)


def _single_token_diff(evidence: str, corrected: str) -> tuple[int, str, str] | None:
    """If `evidence` and `corrected` are the same length in whitespace
    tokens and differ at exactly one position, return
    ``(index, wrong_token, right_token)`` (punctuation-stripped, lowercased).
    Otherwise ``None`` — a real correction almost never changes the word
    count by exactly zero while fixing something bigger, so this is a tight
    enough net for the two false-positive shapes it guards.
    """
    ev = (evidence or "").split()
    co = (corrected or "").split()
    if not ev or len(ev) != len(co):
        return None
    diffs = [
        i
        for i, (a, b) in enumerate(zip(ev, co))
        if _strip_token(a).lower() != _strip_token(b).lower()
    ]
    if len(diffs) != 1:
        return None
    i = diffs[0]
    return i, _strip_token(ev[i]).lower(), _strip_token(co[i]).lower()


def is_first_person_schwa_drop(evidence: str, corrected: str) -> bool:
    """True if `corrected` differs from `evidence` by exactly one token,
    adding a trailing "-e" to a verb directly after "ich"/"Ich"
    ("ich komm" -> "ich komme", "mach" -> "mache", "hab" -> "habe" — any
    stem, not a hardcoded verb list). This is how spoken German actually
    sounds (and how Deepgram spells it) — never a real subjekt-verb-endung
    break.
    """
    diff = _single_token_diff(evidence, corrected)
    if diff is None:
        return False
    i, wrong, right = diff
    if right != wrong + "e":
        return False
    if i == 0:
        return False
    prev = _strip_token((evidence or "").split()[i - 1]).lower()
    return prev == "ich"


def is_das_dass_homophone(evidence: str, corrected: str) -> bool:
    """True if the ONLY difference between evidence and correction is a
    das<->dass swap — a homophone Deepgram could spell either way, not a
    grammar mistake the learner made (SATZ-022's argument, applied here).
    Callers should only set this check when the source is definitely a
    spoken transcript — see ``ledger_guard_reason``'s ``check_das_dass``.
    """
    diff = _single_token_diff(evidence, corrected)
    if diff is None:
        return False
    _, wrong, right = diff
    return {wrong, right} == {"das", "dass"}


def is_quote_verbatim(quote: str, source_text: str, *, strip_punctuation: bool = False) -> bool:
    """True if `quote` (the row's evidence/sentence) is actually in the
    learner's own text, after whitespace + case normalisation. False for an
    empty quote, an empty source, or a misquote (BRIEF-003's class — the
    model paraphrasing, or swapping in a word the learner didn't write,
    instead of copying verbatim).
    """
    q = _normalize(quote, strip_punctuation=strip_punctuation)
    s = _normalize(source_text, strip_punctuation=strip_punctuation)
    if not q or not s:
        return False
    return q in s


_WORD_RE = re.compile(r"\S+")


def repair_quote(quote: str, source_text: str) -> str | None:
    """Return the learner's OWN text for a judge's quote, or None.

    The judges copy imperfectly: they normalise glyphs, and — the BRIEF-003
    class — they sometimes "fix" a word inside the quote ("also" written
    down as "auch"). Dropping every such row would throw away the genuine
    error the row is about (prod trace 020628cb: a real akkusativ-artikel
    row on "keinen Unverträglichkeiten" carried an also→auch misquote in the
    same sentence). So: find the span of the learner's text that matches the
    quote token-for-token, allowing at most ONE differing token when the
    quote is at least three tokens long, and return that span verbatim from
    the source (original casing, original glyphs). Exact matches also come
    back re-anchored to the source, which normalises whitespace/case drift.
    None = no close span exists — a real misquote, and the caller drops it.
    """
    q_tokens = [_strip_token(t).lower() for t in _WORD_RE.findall(quote or "")]
    q_tokens = [t for t in q_tokens if t]
    if not q_tokens or not source_text:
        return None
    src_matches = [m for m in _WORD_RE.finditer(source_text)]
    src_tokens = [_strip_token(m.group(0)).lower() for m in src_matches]
    n = len(q_tokens)
    allowed_diff = 1 if n >= 3 else 0
    best: tuple[int, int] | None = None  # (diff, start)
    for start in range(0, len(src_tokens) - n + 1):
        window = src_tokens[start : start + n]
        diff = sum(1 for a, b in zip(window, q_tokens) if a != b)
        if diff <= allowed_diff and (best is None or diff < best[0]):
            best = (diff, start)
            if diff == 0:
                break
    if best is None:
        return None
    _, start = best
    span_start = src_matches[start].start()
    span_end = src_matches[start + n - 1].end()
    span = source_text[span_start:span_end]
    # Trim the outer punctuation the quote itself did not carry, so a quote
    # like "kann also etwas mitbringen" doesn't come back with a stray
    # closing period from the source's sentence end.
    lead = (quote or "").strip()
    if lead and lead[-1] not in _TOKEN_PUNCT:
        span = span.rstrip(_TOKEN_PUNCT)
    if lead and lead[0] not in _TOKEN_PUNCT:
        span = span.lstrip(_TOKEN_PUNCT)
    return span


def is_correction_vacuous(evidence: str, corrected: str) -> bool:
    """True if `corrected` doesn't actually correct anything: empty, the
    same sentence as the evidence, or the evidence's flagged text still
    sitting there unchanged inside a padded "correction". Not a check that
    the correction is grammatically RIGHT — that's Proposal-2, an LLM
    verify call, deliberately not done here. Punctuation is stripped for
    this comparison (unlike the quote-verbatim check) — a "correction"
    that only adds or drops a trailing period is still a no-op.
    """
    ev = _normalize(evidence, strip_punctuation=True)
    co = _normalize(corrected, strip_punctuation=True)
    if not co:
        return True
    # Only byte-equality counts as "nothing corrected". A correction that
    # CONTAINS the evidence unchanged is usually a real fix that ADDS the
    # missing word ("weil ich müde" -> "weil ich müde bin", "Er hat gesagt"
    # -> "Er hat gesagt, dass …") — treating that as vacuous dropped genuine
    # missing-verb / missing-clause rows in review.
    return co == ev


def ledger_guard_reason(
    *,
    pattern_id: str,
    quote: str,
    corrected: str,
    source_text: str,
    check_das_dass: bool = False,
    strip_punctuation: bool = False,
) -> str | None:
    """Return why a row should be dropped before it reaches
    ``record_grammar_error``, or ``None`` if it clears the ledger boundary.

    ``quote`` is the row's evidence/sentence field (naming differs between
    ``PatternOutcome``/``NewError`` and ``ExtractedError``, callers pass the
    right one in). ``source_text`` is the learner's own text the quote must
    come from — the debrief's transcript, or the harvest's transcript /
    submitted letter.
    """
    if pattern_id == "subjekt-verb-endung" and is_first_person_schwa_drop(quote, corrected):
        return "first-person schwa-drop is standard spoken German, not subjekt-verb-endung"
    if check_das_dass and is_das_dass_homophone(quote, corrected):
        return "das/dass differs only by the spoken homophone spelling"
    if not is_quote_verbatim(quote, source_text, strip_punctuation=strip_punctuation):
        return "evidence quote is not a verbatim substring of the learner's own text"
    if is_correction_vacuous(quote, corrected):
        return "corrected sentence does not actually correct the evidence"
    return None
