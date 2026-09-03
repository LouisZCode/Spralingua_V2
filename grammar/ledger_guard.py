"""Deterministic guards at the grammar-ledger write boundary (LEDGER-001).

Three writers turn an LLM's classified grammar slips into ``user_errors``
rows — the tandem debrief (``agents/debrief.py``), the situation
transcript / Briefkasten letter harvest (``agents/error_extractor.py``),
and (STT-006) the Satzschmiede spoken-attempt route (``satz/routes.py``).
A 2026-08-15 trace review found three shapes of false row reaching the
ledger from the first two judges, and a 2026-09-03 review (STT-006) found
two more from the examiner:

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
5. (STT-006) Deepgram drops a short unstressed leading word (subject
   pronoun or article) at t≈0 of a spoken clip — 200ms of leading silence
   swallows it before the recognizer locks on, no confidence signal to
   gate on. The examiner reads the resulting transcript as missing its
   subject and fails the attempt, "correcting" it by inserting the
   learner's own word right back in.
6. (STT-006) The same "correction" also nudges a prepositional phrase or
   time adverbial to the other end of the sentence — a pure style change
   the STYLE-IS-NOT-AN-ERROR block in ``satz/examiner.py`` already forbids
   the examiner from making, but which reached the ledger anyway.

Checks 5 and 6 (``is_leading_pronoun_insertion``,
``is_reordered_pp_or_time_adverbial``) are gated behind
``ledger_guard_reason``'s ``check_asr_artifacts`` keyword, the same way
check 2 is gated behind ``check_das_dass``: both rationales assume a
Deepgram transcript of spoken audio, which is not true of every writer —
Briefkasten's letter harvest is typed, so it must pass ``False`` (the
default) or a learner who genuinely typed a subjectless sentence gets
forgiven. See ``ledger_guard_reason``'s docstring for which callers pass
which value, and both new checks' own docstrings for two follow-up fixes:
2026-09-03, an unguarded reorder check was also catching a real
subordinate-clause verb-final error, and the leading-pronoun check's
multiset remainder tolerance was also forgiving a real word-order break
riding along with a dropped pronoun; 2026-09-04, an adjacent
``[participle][PP]`` transposition (a genuine perfekt-satzklammer error —
the participle belongs at the clause end) is describable either as "the PP
moved left" (qualifies) or "the participle moved right" (doesn't), and the
any-match decomposition search accepted the first description without
checking whether the second also applied — see
``_has_relocated_qualifying_block``'s docstring for the fix (a per-token
verb-like veto, ``_looks_verb_like``, that rejects the whole reorder if ANY
valid decomposition's moved span looks like part of the verb complex).

None of these need an LLM verify call (that's LEDGER-001 Proposal-2 —
grammatical/meaning-preserving verification of the correction — explicitly
out of scope here). All six are decidable from the evidence/correction
text alone, so they are caught here, deterministically, before any
writer's ``record_grammar_error`` call. ``debrief()``, ``extract_errors()``
and ``satz/routes.py::submit_attempt`` all run every kept row through
``ledger_guard_reason`` right before the write — nothing sits between that
filter and the actual DB write in any current caller
(``pipeline/factory.py``, ``briefkasten/routes.py``, ``szenario/routes.py``,
``satz/routes.py``), so filtering here is filtering at the ledger boundary.

Kept in ``grammar/`` (not ``agents/``) for the same reason the taxonomy
loader lives here: writers already import from this package, and nothing
here needs the Pipecat/LangChain stack ``agents/__init__.py`` drags in.
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


# --- STT-006: leading-word insertion + pure PP/adverbial reorder --------
#
# `_single_token_diff` only covers equal-LENGTH swaps — it can't see an
# INSERTED token at all, so it never fires for either shape below. Both are
# new sibling helpers, not variants of it.

# Small closed set of subject pronouns and articles a spoken clip's leading
# silence can plausibly swallow whole. Deliberately narrow (SATZ subject
# pronouns + der/die/das/ein-family) — this is not "any function word",
# it's specifically the shape Deepgram drops at t≈0 of a recording.
_LEADING_INSERTION_TOKENS = {
    "ich", "du", "er", "sie", "es", "wir", "ihr",
    "der", "die", "das",
    "ein", "eine", "einen", "einem", "einer",
    "dem", "den",
}


def is_leading_pronoun_insertion(evidence: str, corrected: str) -> bool:
    """True iff `corrected` is `evidence` with exactly ONE extra token at
    position 0, and that token (case-insensitive) is a pronoun or article
    from the small closed set above — the STT-006 shape: Deepgram drops a
    short unstressed word at the very start of the clip (leading silence
    eats it before the recognizer locks on — the STT-006 clip itself
    scored 0.996 confidence, so there is no confidence signal to gate on
    instead), the examiner reads the transcript as missing a subject/
    article and "corrects" it back in — with the learner's own sentence
    otherwise untouched.

    The tokens AFTER the inserted one are allowed to differ from `evidence`
    by AT MOST one qualifying PP/time-adverbial relocation — not a bare
    multiset match — on purpose: the STT-006 trace paired the insertion
    with an unrelated reorder of "für morgen" in the same "correction"
    (evidence "Drücke dir für morgen die Daumen" → corrected "Ich drücke
    dir die Daumen für morgen"). The only SUBSTANTIVE change there is the
    leading "Ich" — the reorder is a separate style-only edit the examiner
    should never have made in the first place (see the
    STYLE-IS-NOT-AN-ERROR block in ``satz/examiner.py``) — so stripping the
    leading token and running the SAME clause-safe relocation check
    ``is_reordered_pp_or_time_adverbial`` uses still recognizes this as
    "nothing but the pronoun changed, plus a style-only PP move" and drops
    the row.

    A bare multiset match over-tolerated: it also accepted a GENUINE
    word-order error riding along with the insertion, e.g. evidence
    "habe gesehen den Film" → corrected "Ich habe den Film gesehen" — a
    real Perfekt-word-order fix (the past participle belongs at the
    clause end, "gesehen" needs to move) plus the dropped "Ich". Any
    multiset of the same bag of words matches, so that reorder was wrongly
    forgiven too. Requiring the remainder to be either identical in order
    or a QUALIFYING relocation (PP-led or a bare time adverb — never an
    arbitrary block like "gesehen") rejects this: "gesehen" alone is
    neither, so no qualifying decomposition reproduces the diff, and this
    function returns False — the row is kept, not dropped. (2026-09-04:
    "gesehen" is now ALSO independently vetoed as verb-like —
    `_looks_verb_like`, via `_has_relocated_qualifying_block` — belt and
    braces; see that function's docstring for the sharper case this veto
    was added for, "hat das Buch gelegt auf den Tisch" → "Er hat das Buch
    auf den Tisch gelegt", where the competing decomposition DOES pass the
    PP-qualifying check and the old any-match rule wrongly dropped the
    row.)

    This is deliberately NOT a general "one word added anywhere" check —
    ``is_correction_vacuous``'s docstring already covers why a correction
    that CONTAINS the evidence unchanged is usually a REAL fix that adds a
    genuinely missing word ("weil ich müde" -> "weil ich müde bin", a
    different LENGTH pair this function also rejects outright below): this
    only fires for the closed set at position 0, so a genuine missing-verb
    or missing-clause insertion elsewhere in the sentence is untouched.
    """
    ev_raw = (evidence or "").split()
    co_raw = (corrected or "").split()
    ev = [_strip_token(t).lower() for t in ev_raw]
    co = [_strip_token(t).lower() for t in co_raw]
    if not ev or len(co) != len(ev) + 1:
        return False
    if co[0] not in _LEADING_INSERTION_TOKENS:
        return False
    if co[1:] == ev:
        return True
    return _qualifying_reorder(ev_raw, co_raw[1:])


# Prepositions (plain and fused-with-article) that can lead a moveable
# prepositional phrase — "für morgen", "am Montag", "im Sommer".
_PP_LEAD_PREPOSITIONS = {
    "an", "auf", "aus", "bei", "bis", "durch", "entlang", "für", "gegen",
    "gegenüber", "hinter", "in", "mit", "nach", "neben", "ohne", "seit",
    "trotz", "über", "um", "unter", "von", "vor", "während", "wegen",
    "zwischen", "zu",
    "am", "ans", "aufs", "beim", "im", "ins", "vom", "zum", "zur",
}
# Bare (single-token) time adverbs that can stand alone at either end of a
# sentence without a preposition — "heute", "morgen" (= tomorrow, here).
_BARE_TIME_ADVERBS = {
    "heute", "morgen", "gestern", "übermorgen", "vorgestern", "jetzt",
    "bald", "sofort", "dann", "danach", "davor", "nachher", "vorher",
    "gerade", "neulich", "damals", "demnächst",
}

# Closed set of subordinating conjunctions that send the finite verb to the
# end of THEIR clause — "dass", "weil", "wenn", ... A sentence containing one
# of these (or a comma, which is how a relative clause / subordinate clause
# is set off even without one of these words) has more than one clause, and
# a token relocated across a clause boundary can change which clause's END
# the finite verb sits at — see `is_reordered_pp_or_time_adverbial`'s
# docstring for the 2026-09-03 false positive this closed list exists to
# prevent ("Ich weiß, dass er kommt heute" -> "... dass er heute kommt" is a
# real nebensatz-verbende fix, not a style reorder, because moving "heute"
# past "kommt" moves the finite verb off the clause-final slot).
_SUBORDINATING_CONJUNCTIONS = {
    "dass", "weil", "ob", "wenn", "als", "obwohl", "damit", "bevor",
    "nachdem", "während", "falls", "sobald",
}


def _has_clause_boundary(raw_tokens: list[str]) -> bool:
    """True iff `raw_tokens` (whitespace-split, punctuation still attached —
    a comma only survives on the token that carries it) contain a comma or a
    subordinating conjunction. Either one means the sentence has more than
    one clause, which is exactly the situation `is_reordered_pp_or_time_
    adverbial` and the relocation-tolerant branch of `is_leading_pronoun_
    insertion` must stay out of — see `_SUBORDINATING_CONJUNCTIONS` above.
    Deliberately does not try to determine which clause the moved block
    sits in or reason about verb position across the boundary; when this is
    True, the caller simply does not run the reorder check at all. Narrow
    scope beats clever here — a relative clause's `der/die/das` is not in
    the closed set, but it is always preceded by a comma, which this already
    catches.
    """
    for tok in raw_tokens:
        if "," in tok:
            return True
        if _strip_token(tok).lower() in _SUBORDINATING_CONJUNCTIONS:
            return True
    return False


def _qualifying_reorder(ev_raw: list[str], co_raw: list[str]) -> bool:
    """Shared core of `is_reordered_pp_or_time_adverbial`: `ev_raw`/`co_raw`
    are RAW (not yet punctuation-stripped) whitespace tokens of the two
    sides being compared — either the full evidence/corrected sentence, or
    (from `is_leading_pronoun_insertion`) the full evidence against the
    corrected sentence with its leading inserted token removed. Bails out
    (False) on any clause boundary in either side, then delegates to
    `_has_relocated_qualifying_block` on the normalized tokens.
    """
    if _has_clause_boundary(ev_raw) or _has_clause_boundary(co_raw):
        return False
    ev = [_strip_token(t).lower() for t in ev_raw]
    co = [_strip_token(t).lower() for t in co_raw]
    if len(ev) != len(co):
        return False
    return _has_relocated_qualifying_block(ev, co)


def _is_pp_or_time_adverbial(block: list[str]) -> bool:
    """True iff `block` (already normalized tokens) is a moveable
    prepositional phrase (starts with a preposition, plain or fused with
    an article) or a single bare time adverb — the two shapes STT-006 saw
    the examiner shuffle around for no grammatical reason."""
    if not block:
        return False
    # A preposition needs an object to be a phrase: a LONE "auf"/"an"/"mit"
    # at the end of a clause is a separable-verb particle ("Ich stehe auf um
    # sieben" -> "... um sieben auf" is a real trennbare-Verben error, not a
    # style move), so a single-token block never qualifies via this branch.
    if len(block) >= 2 and block[0] in _PP_LEAD_PREPOSITIONS:
        return True
    return len(block) == 1 and block[0] in _BARE_TIME_ADVERBS


# Finite auxiliaries/modals — closed set. A token here is unambiguously part
# of the verb complex regardless of shape, so it belongs in
# `_looks_verb_like` alongside the participle-shape regexes below.
_FINITE_AUX_MODAL_VERBS = {
    "hat", "habe", "hast", "haben",
    "ist", "bin", "bist", "sind", "seid", "war", "waren",
    "wird", "werden",
    "kann", "kannst", "können",
    "muss", "musst", "müssen",
    "will", "willst", "wollen",
    "soll", "sollst", "sollen",
    "darf", "darfst", "dürfen",
    "möchte", "möchtest", "möchten",
    "mag", "magst", "mögen",
}
# ge-participle shape: "gelegt", "gegessen", "gefahren", "gesehen",
# "gekauft" — ge- + a stem of at least two characters + the participle
# ending -t or -en. Deliberately does NOT match "Gegend" or "Gebäude"
# (they end -d/-e, not -t/-en), nor "Garten" (doesn't start "ge-" at all —
# the second letter is "a", not "e"). It DOES over-match "Gedanken" ("ge" +
# "dank" + "en") even though that's a noun, not a participle — acceptable,
# because over-matching only makes this guard treat MORE reorders as
# involving a verb, i.e. keep MORE rows in the ledger, which is the safe
# direction (a false negative here drops a row that should have been kept
# as a real error; that's worse than a false positive that merely keeps a
# row that could have been safely dropped).
_VERB_PARTICIPLE_RE = re.compile(r"^ge\w{2,}(t|en)$")
# -ieren verb shape: "studiert", "informiert" — a stem of at least three
# characters plus the participle/finite ending "-iert".
_VERB_IERT_RE = re.compile(r"^\w{3,}iert$")
# Inseparable-prefix verb shape: prefix + stem + -t/-en, length >= 6 overall
# so a short unrelated word starting with one of these letter groups
# ("Erbe", "Berg", "Verein") can't accidentally qualify. Catches
# "verstanden", "bestellt", "entdeckt" etc. that the ge-participle regex
# above doesn't (no "ge-" prefix on an inseparable-prefix verb's participle).
_INSEPARABLE_VERB_PREFIXES = ("be", "ver", "er", "ent", "emp", "zer", "miss", "ge")

# Separable-verb particles (trennbare Verben). A single-token moved block
# from this set is the verb complex relocating ("stehe auf um sieben" ->
# "stehe um sieben auf"), never a style-only phrase move — vetoed in
# `_has_relocated_qualifying_block`. Many double as prepositions, which is
# why `_is_pp_or_time_adverbial` also refuses a one-token "PP".
_SEPARABLE_PARTICLES = {
    "ab", "an", "auf", "aus", "bei", "ein", "fest", "her", "hin", "los", "mit",
    "nach", "statt", "teil", "um", "vor", "vorbei", "weg", "weiter", "zu",
    "zurück", "zusammen",
}


def _looks_verb_like(token: str) -> bool:
    """True iff `token` (already lowercased/punctuation-stripped, but this
    also lowercases defensively) is plausibly part of the German verb
    complex — a finite auxiliary/modal, or a past-participle shape — rather
    than an ordinary noun/article/preposition/adverb.

    Deliberately conservative/over-inclusive per the module's asymmetric
    cost model (see `_VERB_PARTICIPLE_RE`'s comment): the only job this
    function has is to VETO a reorder-relocation guess as "this block is
    plausibly a verb moving, not a style-only phrase moving", so matching a
    token that isn't really verb-like just means the guard keeps a row it
    could safely have dropped — never the reverse.

    Three ways a token can match:
    - A closed set of finite auxiliaries/modals (`_FINITE_AUX_MODAL_VERBS`):
      hat/habe/hast/haben, ist/bin/bist/sind/seid/war/waren, wird/werden,
      kann.../müssen, will.../wollen, soll.../sollen, darf.../dürfen,
      möchte.../möchten, mag/magst/mögen.
    - `_VERB_PARTICIPLE_RE` — "ge" + >=2-char stem + "t"/"en": gelegt,
      gegessen, gefahren, gesehen, gekauft all match.
    - `_VERB_IERT_RE` — >=3-char stem + "iert": studiert, informiert match.
    - An inseparable prefix (`_INSEPARABLE_VERB_PREFIXES`) + "t"/"en"
      ending, with the whole token >= 6 characters: verstanden, bestellt.

    Must NOT match ordinary nouns that happen to share a "ge-" start or a
    verb-like ending: "Daumen" (no ge- prefix, not in the closed set, not
    -iert), "Kino"/"Buch"/"Tisch"/"Film" (none of the three shapes), nor
    "Gegend"/"Gebäude" (ge- prefixed but end in "-d"/"-e", not "-t"/"-en")
    or "Garten" (doesn't start "ge-" — second letter is "a"). DOES
    over-match "Gedanken" (see `_VERB_PARTICIPLE_RE`'s comment) — accepted.
    """
    t = (token or "").lower()
    if not t:
        return False
    if t in _FINITE_AUX_MODAL_VERBS:
        return True
    if _VERB_PARTICIPLE_RE.match(t):
        return True
    if _VERB_IERT_RE.match(t):
        return True
    if len(t) >= 6 and t.endswith(("t", "en")):
        for prefix in _INSEPARABLE_VERB_PREFIXES:
            if t.startswith(prefix):
                return True
    return False


def _has_relocated_qualifying_block(ev: list[str], co: list[str]) -> bool:
    """True iff `ev` and `co` hold the exact same tokens (same multiset,
    nothing added or removed) in a different order, AND (a) at least one way
    of describing that reordering as "one contiguous span of `ev` moved to a
    different contiguous position, everything else keeping its relative
    order" has a moved span that qualifies as a PP or bare time adverb
    (`_is_pp_or_time_adverbial`), AND (b) NO valid such decomposition's moved
    span contains a verb-like token (`_looks_verb_like`) — checked across
    EVERY decomposition that reproduces `co`, not just the PP-qualifying
    ones, so (b) can veto even when (a) is also satisfied by some other
    decomposition.

    Checks every valid decomposition rather than stopping at the first one
    found, for two independent reasons:

    1. (pre-existing) A single adjacent-block transposition is describable
       two equivalent ways ("A moved right" is the same edit as "B moved
       left"), and only one of the two descriptions may be the
       grammatically meaningful one — see `is_reordered_pp_or_time_
       adverbial`'s docstring for a worked case where this matters.
    2. (2026-09-04 follow-up, STT-006 continued) That same ambiguity has a
       sharper failure mode than "which description is meaningful": an
       adjacent two-block transposition `[participle][PP] -> [PP]
       [participle]` — a genuine perfekt-satzklammer word-order error, the
       past participle belongs at the clause end — is ALSO describable as
       "the PP moved left", and the PP-first description always qualifies.
       Evidence "Er hat das Buch gelegt auf den Tisch", corrected "Er hat
       das Buch auf den Tisch gelegt": the block "auf den Tisch" moving left
       past "gelegt" reproduces `co` and passes `_is_pp_or_time_adverbial`,
       so an any-match rule (the pre-2026-09-04 version of this function)
       accepted it and the row — a REAL error — was dropped from the
       ledger. The competing decomposition, "gelegt" moving right past "auf
       den Tisch", ALSO reproduces `co`, and its moved block is the verb
       itself. Rejecting whenever ANY valid decomposition's block is
       verb-like (regardless of whether another decomposition also
       qualifies as PP/adverbial) closes this: `_looks_verb_like("gelegt")`
       is True, so this function now returns False for that pair — the row
       stays in the ledger. Same shape, same fix: "Er hat gegessen im
       Restaurant" -> "... im Restaurant gegessen"; "Sie ist gefahren nach
       Berlin" -> "... nach Berlin gefahren"; "Er hat das Buch gelegt
       gestern" -> "... gestern gelegt" (bare adverb instead of a PP,
       same ambiguity, same veto). The STT-006 leading-pronoun-insertion
       shape rides the same code path (`is_leading_pronoun_insertion` calls
       `_qualifying_reorder`, which calls this function) and gets the same
       fix for free: "hat das Buch gelegt auf den Tisch" -> "Er hat das
       Buch auf den Tisch gelegt" is also kept, not dropped.

       This veto is intentionally about the SPAN THAT MOVES, not about
       whether a verb-like token appears anywhere in the sentence — `hat`/
       `ist` sit in the unmoved "rest" of every example above and don't
       trip it; only a verb-like token INSIDE a reproducing decomposition's
       block does. A decomposition search that stopped at the first
       PP-qualifying match (as before) would never even construct the
       competing verb-moved decomposition to check it, which is why the
       exhaustive search (already needed for reason 1) is what makes reason
       2's fix possible without a parser.
    """
    if not ev or ev == co or sorted(ev) != sorted(co):
        return False
    n = len(ev)
    found_qualifying = False
    for length in range(1, n):
        for i in range(0, n - length + 1):
            block = ev[i : i + length]
            rest = ev[:i] + ev[i + length :]
            for k in range(0, len(rest) + 1):
                if rest[:k] + block + rest[k:] != co:
                    continue
                if any(_looks_verb_like(tok) for tok in block):
                    return False
                # A lone separable-verb particle moving is the verb complex
                # moving (trennbare Verben): veto, same as a participle.
                if len(block) == 1 and block[0] in _SEPARABLE_PARTICLES:
                    return False
                # Modal/auxiliary present + a lone "-en" token moving = an
                # infinitive leaving/reaching clause-final position ("möchte
                # gehen nach Hause" -> "möchte nach Hause gehen"): the verb
                # complex again, not style. Over-matches a lone plural noun
                # ("Daumen") only when a modal is present — safe direction.
                if (
                    len(block) == 1
                    and len(block[0]) >= 4
                    and block[0].endswith("en")
                    and block[0] not in _BARE_TIME_ADVERBS  # "morgen" is not a verb
                    and any(tok in _FINITE_AUX_MODAL_VERBS for tok in ev)
                ):
                    return False
                if _is_pp_or_time_adverbial(block):
                    found_qualifying = True
    return found_qualifying


def is_reordered_pp_or_time_adverbial(evidence: str, corrected: str) -> bool:
    """True iff `evidence` and `corrected` hold the exact same words (same
    multiset, nothing added or removed — a genuine "pure reordering", as
    opposed to `is_leading_pronoun_insertion`'s insertion case) and the
    only thing that moved is ONE prepositional phrase or bare time adverb
    ("für morgen", "heute", "am Montag") sliding to a different position —
    the STT-006 shape, and a pure style change the STYLE-IS-NOT-AN-ERROR
    block in ``satz/examiner.py`` already forbids the examiner from making.

    Deliberately narrow, because a general "same bag of words, different
    order" rule is NOT safe. The rule this function implements, and why,
    against the four decisive STT-006 examples:

    - ("Drücke dir für morgen die Daumen", "Ich drücke dir die Daumen für
      morgen") — different TOKEN COUNT (an insertion, not a pure reorder)
      -> this function returns False immediately; `is_leading_pronoun_
      insertion` is the one that catches this shape (see its docstring for
      why the reorder riding along with the insertion doesn't block it).
    - ("weil ich müde", "weil ich müde bin") — also a different token count
      (a genuine missing-verb insertion at the END, not a reorder) ->
      False.
    - ("heute ich gehe ins Kino", "heute gehe ich ins Kino") — same length,
      same multiset, genuinely reordered — but this is a real verb-second
      violation: the finite verb "gehe" has to sit right after the first
      constituent "heute", and "ich" was wrongly parked in front of it.
      The only tokens that actually swapped are "ich" and "gehe" — neither
      a preposition-led span nor a bare time adverb — so no qualifying
      decomposition reproduces the diff -> False. (A non-qualifying
      decomposition — relocating "ich" alone, or "gehe" alone — DOES
      reproduce the diff; that's exactly why `_has_relocated_qualifying_
      block` checks every decomposition instead of trusting the first one:
      neither of those blocks passes `_is_pp_or_time_adverbial`, so nothing
      fires.)
    - ("Ich drücke dir die Daumen für morgen", "Ich drücke dir für morgen
      die Daumen") — same length, same multiset; the transposition can be
      described as either "die Daumen" moving right or "für morgen" moving
      left (adjacent blocks swapping is always describable both ways) —
      but only "für morgen" qualifies (it starts with the preposition
      "für"; "die Daumen" starts with an article, not a preposition) -> a
      qualifying decomposition exists -> True.
    - ("Ich weiß, dass er kommt heute", "Ich weiß, dass er heute kommt") —
      same length, same multiset, and by the block-decomposition rule
      alone "heute" moving left past "kommt" LOOKS like a qualifying bare-
      time-adverb relocation (it's the same shape as the "Ich gehe morgen
      ins Kino" case elsewhere in this docstring, which is fine to ignore).
      It is not fine here: "dass er kommt heute" / "dass er heute kommt"
      is a SUBORDINATE clause, and moving "heute" across "kommt" moves the
      finite verb OFF the clause-final slot ``nebensatz-verbende`` requires
      — a real grammar error, not a style choice. The clause-block
      decomposition this function uses can't see clause structure (no
      parser), so instead of trying to special-case it, this function
      bails out of the reorder check entirely whenever either sentence
      contains a comma or a subordinating conjunction
      (`_SUBORDINATING_CONJUNCTIONS` — "dass" here) — see
      `_has_clause_boundary`. -> False, by the bail-out, before the
      decomposition search even runs.
    - ("Er hat das Buch gelegt auf den Tisch", "Er hat das Buch auf den
      Tisch gelegt") — 2026-09-04 follow-up (STT-006 continued). Same
      length, same multiset — but this is a real perfekt-satzklammer error:
      the past participle "gelegt" belongs at the very end of the clause,
      and it was left stranded before the PP instead. By the block-
      decomposition rule alone this LOOKS exactly like the "für morgen"
      case above: "auf den Tisch" moving left past "gelegt" reproduces the
      diff and starts with the preposition "auf", so it qualifies. The
      difference is that the SAME diff is ALSO reproduced by "gelegt"
      moving right past "auf den Tisch" — an adjacent two-block
      transposition is always describable both ways, same as the "die
      Daumen"/"für morgen" case, except this time the OTHER description's
      moved block is the participle itself. `_has_relocated_qualifying_
      block` checks every decomposition (not just PP-qualifying ones) for a
      verb-like moved block (`_looks_verb_like`) and rejects the whole
      reorder if it finds one, regardless of whatever the PP-qualifying
      decomposition also found -> False; the row stays in the ledger. Same
      shape: "Er hat gegessen im Restaurant" -> "... im Restaurant
      gegessen"; "Sie ist gefahren nach Berlin" -> "... nach Berlin
      gefahren"; "Er hat das Buch gelegt gestern" -> "... gestern gelegt"
      (bare time adverb instead of a PP — same ambiguity, same veto, since
      `_is_pp_or_time_adverbial` accepts bare time adverbs too). See
      `_has_relocated_qualifying_block`'s docstring for the full mechanics
      and why the veto has to be per-decomposition rather than "does the
      sentence contain a verb-like token anywhere" (`hat`/`ist` sit
      unmoved in every one of these examples and must NOT trip it).

    This does not check "the verb keeps its index" directly (that needs a
    parser this module doesn't have); outside a subordinate clause it gets
    the same safety by construction instead — restricting the moved span
    to a PP/adverbial means the subject and the finite verb are, by
    definition, part of the unmodified "rest", so their relative order to
    each other is never the thing this function is agreeing to ignore.
    Inside a subordinate clause that construction argument breaks (the
    "rest" can still hold the finite verb, but the clause-final position
    that verb has to occupy is defined relative to the CLAUSE boundary, not
    to the other tokens) — hence the bail-out above instead of trying to
    make the construction argument clause-aware.
    """
    return _qualifying_reorder((evidence or "").split(), (corrected or "").split())


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
    check_asr_artifacts: bool = False,
    strip_punctuation: bool = False,
) -> str | None:
    """Return why a row should be dropped before it reaches
    ``record_grammar_error``, or ``None`` if it clears the ledger boundary.

    ``quote`` is the row's evidence/sentence field (naming differs between
    ``PatternOutcome``/``NewError`` and ``ExtractedError``, callers pass the
    right one in). ``source_text`` is the learner's own text the quote must
    come from — the debrief's transcript, or the harvest's transcript /
    submitted letter.

    ``check_asr_artifacts`` gates the two STT-006 checks
    (``is_leading_pronoun_insertion``, ``is_reordered_pp_or_time_
    adverbial``) the same way ``check_das_dass`` already gates the
    homophone check: both rationales are specific to a DEEPGRAM transcript
    of spoken audio — a typed letter (Briefkasten) has no leading-silence
    dropout and no ASR misheard reorder, so a caller whose ``source_text``
    is typed must pass ``False`` (the default) or it will forgive a
    learner who genuinely typed a sentence missing its subject. Callers
    known to always be spoken audio (``satz/routes.py``,
    ``agents/debrief.py``, and the spoken ``extract_errors`` callers —
    ``szenario/routes.py``, ``interview/routes.py``) pass ``True``.
    """
    if pattern_id == "subjekt-verb-endung" and is_first_person_schwa_drop(quote, corrected):
        return "first-person schwa-drop is standard spoken German, not subjekt-verb-endung"
    if check_das_dass and is_das_dass_homophone(quote, corrected):
        return "das/dass differs only by the spoken homophone spelling"
    if check_asr_artifacts and is_leading_pronoun_insertion(quote, corrected):
        return "corrected only reinserts a leading pronoun/article an ASR clip likely dropped (STT-006)"
    if check_asr_artifacts and is_reordered_pp_or_time_adverbial(quote, corrected):
        return "corrected only moves a prepositional phrase/time adverbial — a style change, not a grammar error (STT-006)"
    if not is_quote_verbatim(quote, source_text, strip_punctuation=strip_punctuation):
        return "evidence quote is not a verbatim substring of the learner's own text"
    if is_correction_vacuous(quote, corrected):
        return "corrected sentence does not actually correct the evidence"
    return None
