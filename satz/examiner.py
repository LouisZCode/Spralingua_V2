"""Spoken-attempt examiner for Satzschmiede (SATZ-002, Phase 3: speak & check).

The learner records ONE German sentence for the card in front of them. Two
one-shot calls turn the clip into feedback:

1. **Deepgram prerecorded REST** — the finished clip goes up in a single POST
   and the transcript comes back in the response body. No streaming, no VAD,
   no Pipecat: the conversation pipeline exists for live turn-taking; this is
   a plain request/response, so it uses none of it.
2. **Examiner LLM** — the same Cerebras ``gpt-oss-120b`` structured-output
   wiring as ``satz/enricher.py`` makes two separate judgements: was the
   card's WORD used correctly (that is what the card tests — it drives the
   green/red verdict), and is the rest of the sentence grammatical (feedback
   only, shown as a grammar note — it never fails the attempt).
"""

import re
import time
from typing import Optional
from urllib.parse import quote

import aiohttp
from agents.openrouter_llm import structured_judge_llm
from loguru import logger
from pydantic import BaseModel, Field

from agents.audio_costs import (
    DEEPGRAM_NOVA3_PRERECORDED_PER_MIN,
    stamp_audio_cost,
    stt_cost_usd,
)
from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from config import deepgram_api_key
from grammar import load_taxonomy, taxonomy_brief

EXAMINER_MODEL = "openai/gpt-oss-120b"

# One-shot (prerecorded) endpoint — same nova-3 model as the live pipeline's
# streaming STT (services/stt.py::DEEPGRAM_MODEL). Language is a per-call
# parameter on `transcribe_attempt` (default "de"), not a fixed constant:
# AGENT-001 is about to route Clara's teacher room — an English-STT lesson —
# through the same upload path this module serves (/tandem/say-audio), so a
# single hardcoded German URL would mis-transcribe every one of her turns.
# Every existing caller (Satzschmiede itself, sprechen, szenario, verbformen)
# still tests the German target language, so the default keeps their calls
# byte-for-byte identical.
_DEEPGRAM_MODEL = "nova-3"
# STT-005: filler_words=true — without it nova-3 strips "um"-like tokens even
# from FLUENT German ("…folgen, um das Zimmer zu erreichen" came back without
# the "um" in a controlled A/B, 2026-08-03), and a missing "um" is a grammar
# error here, not a filler. The docs say the param is English-only; the A/B
# says it works on language=de. Keep it.


def _deepgram_url(language: str) -> str:
    """Prerecorded-endpoint URL for one call. Kept as a single helper — not
    inlined per-call — so the query-param shape (model/language/smart_format/
    filler_words) stays in exactly one place; callers only ever append
    `&keyterm=` on top of what this returns."""
    return (
        f"https://api.deepgram.com/v1/listen?model={_DEEPGRAM_MODEL}"
        f"&language={language}&smart_format=true&filler_words=true"
    )


# STT-004.1: measured baseline for this call is ~0.8-1.5s, but a bad window
# on 2026-08 produced 5.4s/9.6s/16.2s/22.4s/30.9s calls (the last grazing the
# `aiohttp.ClientTimeout(total=30)` below) with no visibility beyond digging
# through Langfuse after the fact. Anything past this is logged + tagged.
_SLOW_TRANSCRIBE_THRESHOLD_S = 4.0


async def transcribe_attempt(
    audio: bytes,
    mimetype: str | None,
    keyterms: list[str] | None = None,
    language: str = "de",
) -> str:
    """One POST to Deepgram's prerecorded API: finished clip in, transcript out.

    Browsers hand us opus-in-webm (Chrome/Firefox) or aac-in-mp4 (Safari);
    Deepgram accepts both as-is, so the upload's content type passes through.

    STT-003 P2: ``keyterms`` are nova-3 keyterm-prompting hints (repeated
    ``&keyterm=`` query params). Satzschmiede's strongest case — the card names
    the exact word the learner was told to say, so we bias the recognizer toward
    it and its spoken past form, cutting *false* fails on rare target words.

    AGENT-001: ``language`` defaults to German so every pre-existing caller
    (satz, sprechen, szenario, verbformen) is unaffected without touching a
    call site. The one caller that passes something else is
    ``/tandem/say-audio``, which derives "en" server-side from the live
    session's lesson (Clara's English teacher room) — never from client input.
    """
    url = _deepgram_url(language)
    terms = [t for t in ((kt or "").strip() for kt in keyterms or []) if t]
    for term in terms:
        url += f"&keyterm={quote(term)}"  # quote handles umlauts/ß/spaces
    headers = {
        "Authorization": f"Token {deepgram_api_key}",
        "Content-Type": mimetype or "audio/webm",
    }
    timeout = aiohttp.ClientTimeout(total=30)
    # OBS-006: the `stt` child of the route's satz-attempt trace — span
    # duration is the Deepgram round-trip, the half of the attempt's latency
    # the examiner LLM can't explain.
    with generation_span(
        "stt",
        system="deepgram",
        model=_DEEPGRAM_MODEL,
        operation="transcription",
        input_text=f"[{len(audio)} bytes, {mimetype or 'audio/webm'}]",
    ) as span:
        if terms:
            span.set_attribute("keyterms", ", ".join(terms))
        span.set_attribute("audio.bytes", len(audio))
        call_started = time.monotonic()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, data=audio) as resp:
                resp.raise_for_status()
                body = await resp.json()
        elapsed_s = time.monotonic() - call_started
        is_slow = elapsed_s > _SLOW_TRANSCRIBE_THRESHOLD_S
        span.set_attribute("stt.duration_s", elapsed_s)
        span.set_attribute("stt.slow", is_slow)
        if is_slow:
            logger.warning(
                f"Deepgram prerecorded call was slow: {elapsed_s:.1f}s "
                f"(threshold {_SLOW_TRANSCRIBE_THRESHOLD_S:.1f}s)"
            )
        transcript = body["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        record_generation_output(span, transcript)
        # Exact per-attempt cost: Deepgram's prerecorded response carries the
        # clip's own length at `metadata.duration` (seconds). Missing key ->
        # skip the stamp silently, never crash the attempt over a cost metric.
        duration = body.get("metadata", {}).get("duration")
        if duration is not None:
            audio_seconds = round(duration, 2)
            usd = stt_cost_usd(audio_seconds, DEEPGRAM_NOVA3_PRERECORDED_PER_MIN)
            stamp_audio_cost(
                span,
                usage={"audio_seconds": audio_seconds},
                cost={"audio_seconds": usd, "total": usd},
            )
    return transcript


class Judgement(BaseModel):
    """Two separate judgements: the WORD is what the card tests (drives the
    pass/fail colour); the rest of the sentence's grammar is feedback only."""

    word_ok: bool = Field(
        description=(
            "True iff the target word itself was used correctly: present, in "
            "its intended sense, with the grammar the word owns done right. "
            "A speech-recognition artifact (trimmed ending, homophone) is "
            "never grounds for false"
        )
    )
    grammar_ok: bool = Field(
        description=(
            "True iff the rest of the sentence is grammatical German. A "
            "speech-recognition artifact elsewhere in the transcript is "
            "never grounds for false"
        )
    )
    error: Optional[str] = Field(
        default=None,
        description=(
            "One short English line (AT MOST 10 words) naming the broken rule; "
            "null when there is nothing to name"
        ),
    )
    corrected: Optional[str] = Field(
        default=None,
        description=(
            "The learner's sentence minimally repaired, in German — whenever "
            "anything is wrong. Null when everything is right"
        ),
    )
    # GRAM-001: the harvest seam. Classified here, in the same call that
    # already names the error — no second LLM call, no response change.
    pattern_id: Optional[str] = Field(
        default=None,
        description=(
            "When a mistake matches exactly ONE entry of the grammar-pattern "
            "catalog, that entry's id verbatim; null when nothing is wrong, "
            "the slip is purely about this word, or no catalog entry fits"
        ),
    )


PROMPT = """# Role
You examine one spoken attempt in a German vocabulary trainer. The learner saw a word card and had to say ONE German sentence of their own that uses that word. Judge the attempt and give feedback.

# The card
{card}

# What the learner said (speech-recognition transcript)
"{transcript}"

# How to judge — two SEPARATE calls
- The transcript comes from speech recognition: IGNORE punctuation and capitalization entirely, and forgive an obviously misheard small word — judge the sentence the learner most plausibly said. A filler ("ähm") is fine.

## Where graders go wrong on ASR noise
A trimmed ending or homophone Deepgram wrote instead of what was said ("das"/"dass", "seid"/"seit") is the RECOGNIZER's doing, not the learner's.
- target 'Bahnhof' · "...am Bahnho" → **word_ok: true** — clipped letter, not a wrong word.
- "ich glaube das er schon da ist" → **grammar_ok: true** — "das" is Deepgram's spelling of "dass".
- "mein bester Freund seid der Schule" → **grammar_ok: true** — "seid" is Deepgram's spelling of "seit".
- target 'Ebene' · "die Modelle Ebene ist die wichtigste" → **word_ok: true** — Deepgram split the compound "Modellebene" into two words (SATZ-022); that's the recognizer's spelling, never a missing or wrong word. NEVER "repair" a split compound by deleting one of the halves.
- target 'Ebene' · "die Modelle Ebene ist die wichtigste weil ich habe sie gebaut" → **word_ok: true, grammar_ok: false** — CONTROL: the split compound is still forgiven, but the weil-clause word order is a real, unrelated error.
- target 'drücken' · "drücke dir für morgen die Daumen" → **grammar_ok: true** — a spoken clip's leading silence can swallow a short unstressed subject pronoun at t≈0 before the recognizer locks on; the learner most plausibly said "Ich drücke dir für morgen die Daumen". Judge on that basis: the target word is present and the rest of the sentence, AS SPOKEN, is already well-formed German — do NOT also "fix" it by moving "für morgen" or anything else; a dropped leading pronoun is the recognizer's doing, the word order it left behind is the learner's and was correct.
- "heute ich gehe ins Kino" → **grammar_ok: false** — CONTROL: no artifact excuses this V2 violation. Do not confuse the two: a subject genuinely ABSENT from the very start of the clip is forgiven (above); a subject that IS present but sits in the wrong slot, after the sentence already opened with something else, is a real verb-second error.

- `word_ok` — did the learner use the TARGET WORD in its taught sense? This covers ONLY: is the word present (any correctly conjugated or declined form counts; separable verbs may split), in its intended meaning, and — when the card marks it reflexive — paired with the matching reflexive pronoun. It does NOT cover the target's own article, case, or declension ending, even when they sit directly on the target word — that belongs to grammar_ok (SATZ-024, see AXIS WORKED EXAMPLES below). false only when the word is missing, used in the wrong sense, or (reflexive cards) missing its pronoun.
- AXIS WORKED EXAMPLES (SATZ-024): "Herr Klaus ist einen Lügner" (card 'Lügner') → word_ok TRUE (the noun is present, right sense), grammar_ok FALSE ('einen' is wrong here — a predicate nominative needs 'ein'). "ich werde die Fachwelt beitreten" (card 'Fachwelt') → word_ok TRUE, grammar_ok FALSE ('beitreten' governs Dativ with no preposition: 'der Fachwelt', not 'die Fachwelt'). Control: "Erinnerst du mich?" (card 'sich erinnern an' = to remember) → word_ok FALSE — this is a wrong SENSE, not a grammar slip: bare 'erinnern' means 'to remind', not 'to remember'.
- MEANING CHECK for `word_ok` (SATZ-008): before settling word_ok, first restate to yourself what the learner is trying to SAY, then ask: is the target the word a German speaker would actually use for that idea? The word being present with clean grammar is NOT enough. Worked example: card 'erkennen' (to recognize) in "Ich habe letztes Jahr eine neue App erkannt, und jetzt verkaufe ich sie" — selling it means they MADE it, and you cannot 'erkennen' something you created yourself: the fitting verb is 'entwickelt' or 'erfunden', so word_ok=false and `corrected` swaps the verb in. Fail on sense only when the mismatch is clear from the sentence itself; a genuinely plausible correct reading keeps word_ok=true.
- {evidence_line}
- `grammar_ok` — is EVERYTHING ELSE grammatical German — INCLUDING the target word's own article, case and declension ending (see AXIS WORKED EXAMPLES above)? Also: word order, other verbs' conjugation, other words' articles and endings. A sentence can be word_ok but not grammar_ok: "Ich hasse Winter, weil ich habe viele Allergien" uses the target 'Allergie' perfectly, but the weil-clause word order is wrong. If the sentence isn't German at all, both are false.
- VERB & PREPOSITION GOVERNMENT (SATZ-024/025) is a grammar_ok matter, never word_ok — even when the target word itself is the one governing the case: "sich gewöhnen an" + AKKUSATIV — "…an die deutsche Sprache gewöhnt" is correct; "…an der deutschen Sprache gewöhnt" and "…an der deutsche Sprache gewöhnt" → BOTH grammar_ok FALSE, corrected "…an die deutsche Sprache gewöhnt" (never dative here, no matter how natural it sounds — check the case after the target's own preposition every time, do not wave the sentence through because the participle is right). "beitreten" + DATIV object, NO preposition — "ist dem Klub beigetreten" is correct; "ist im Club beigetreten" → grammar_ok FALSE (no preposition belongs here at all); "ist den Klub beigetreten" → grammar_ok FALSE (needs 'dem', not 'den'). "recht haben", never "recht sein" — "der Kunde hat fast immer recht" is correct; "der Kunde ist fast immer recht" → grammar_ok FALSE (wrong verb for this fixed phrase) even though the card's own target ('Kunde') is used perfectly — word_ok stays TRUE.
- DOES THE SENTENCE MEAN ANYTHING? (SATZ-025): a non-German FRAGMENT mixed into otherwise-German words → grammar_ok FALSE, error "part of the sentence is not German" ("Meine Laufbahn that good" — 'that good' isn't German). A word that is grammatically well-formed but makes NO SENSE in its slot is also not a pass on grammar_ok — "die Schwangerschaft meiner Frau brach schon gut" ('brach' = broke, nonsensical here) and "Manchmal fülle ich starken Druck in meine Baum" ('fülle' = fill, nonsensical with Druck) — never accept a fluent-sounding but meaningless sentence, and never invent a different, unrelated meaning just to make it fit. An unresolvable noun phrase — "die Lieferkette auf Rechen" ('Rechen' = rake, doesn't belong here) — is the same failure. SPOKEN-ATTEMPT SPECIAL CASE: when the transcript itself looks like a mishearing (fülle/fühle, Baum/Bauch are near-homophones), do not silently repair it into fluent nonsense — say so, and offer the plausible sentence instead: `error` "transcript may be misheard — doesn't make sense as written", `corrected` "Did you say: Manchmal fühle ich starken Druck in meinem Bauch?".
- STYLE IS NOT AN ERROR (SATZ-008): if everything said is grammatical, grammar_ok=true — even when you would phrase it differently, prefer another word order, or would merge or split sentences. A learner may speak TWO short sentences instead of one — that is fine; judge them together. Two short main clauses are GOOD German („Ich habe immer die Wahrheit gesagt. Ich bin kein Lügner." is fully correct — never "fix" it into one sentence). A style preference must never set word_ok or grammar_ok to false, and `corrected` must never merely restyle a correct attempt.
- `corrected`: whenever anything is false — repair the learner's own sentence with the smallest possible change (keep their idea and their words). If the target word was missing entirely, write the simplest sentence that expresses their idea WITH the word. The repair must itself be flawless German — never leave a new error in it. Worked example: a purpose clause needs „um … zu" — "Du kannst der Zeile folgen, das Zimmer zu erreichen" must be repaired to "…, um das Zimmer zu erreichen"; dropping the „um" is itself an error (learners repeat your correction word for word). When everything is right: null. Always write `corrected` in standard German orthography — capitalize the sentence start and every noun — no matter how the transcript was cased; a lowercased correction makes the app flag the learner's correctly-spoken nouns as errors (SATZ-016).
- `error`: when grammar_ok=false, ONE short English line naming the rule that was broken (e.g. "'weil' sends the verb to the end") — the correction sits right next to it, so name the WHY, never restate the fix. When only word_ok=false, add it only when the correction alone doesn't reveal why (e.g. "'freuen' is reflexive — it needs 'mich'"), otherwise null. AT MOST 10 words. When everything is right: null.

# Grammar-pattern catalog (for `pattern_id`)
{taxonomy}

- `perfekt-satzklammer` vs `partizip2-form`: `perfekt-satzklammer` is a POSITION error — haben/sein isn't in position two, or the Partizip II isn't closing the bracket. `partizip2-form` is a FORM error — the participle's own shape is wrong: a bad ge-/-en/-t shape, a strong verb wearing a weak ending, or an auxiliary paired with a finite past-tense form (e.g. "habe … übergab") instead of the participle.
- `pattern_id`: when your judgement found a mistake (word_ok or grammar_ok false) AND the broken rule matches exactly one catalog entry above, return that entry's id. Use ONLY ids from the catalog — never invent one. Purely lexical slips — the target's gender misremembered, a wrong word sense — get null: the card's own schedule already tracks those. But a case, word-order or tense error is a pattern even when it lands on the target word (a nominative article in an object slot is akkusativ-artikel, a missing reflexive pronoun is reflexivpronomen). When everything is right: null.
"""


def _card_brief(card) -> str:
    """Render the card facts the examiner needs (accepts the ORM VocabCard)."""
    lines = [f"- word to use: {card.target} ({card.type})"]
    if card.article:
        lines.append(f"- it is a noun; its article is: {card.article}")
    if card.reflexive:
        lines.append(
            "- it is a REFLEXIVE verb: a correct sentence must pair it with the "
            "matching reflexive pronoun (mich/dich/sich/uns/euch)"
        )
    if card.type == "adjective":
        lines.append(
            "- it is an adjective: endingless predicative use ('Das Auto ist "
            "schnell') is correct; attributive use must carry the right "
            "declension ending ('ein schnelles Auto'). Comparative/superlative "
            "forms count as using the word"
        )
    if card.type == "preposition":
        lines.append(
            "- it is a preposition: a correct sentence must actually use it as a "
            "preposition. A two-way preposition takes the ACCUSATIVE for motion "
            "toward a goal (Wohin?) and the DATIVE for a static location (Wo?). "
            "word_ok=false only if the preposition itself is missing or the "
            "wrong preposition is used for the relation intended; a wrong case "
            "on ITS OBJECT is grammar_ok=false, never word_ok=false (SATZ-024 — "
            "case is a grammar_ok matter even when the target itself governs it)"
        )
    if card.type == "adverb":
        lines.append(
            "- it is an adverb: it never takes an ending, so word_ok=false only "
            "if it is missing, used as a predicate adjective, or replaced by a "
            "synonym/antonym — a genuine comparative (lieber, öfter) counts as "
            "using the word; if it opens the sentence the finite verb must come "
            "right after it, and a broken V2 there is grammar_ok=false, NEVER "
            "word_ok=false (SATZ-023, same axis split as SATZ-024)"
        )
    if card.tense == "past":
        lines.append(
            "- this card tests the SPOKEN PAST: the sentence must use the verb "
            "in a CORRECTLY BUILT past form — Perfekt, Präteritum and "
            "Plusquamperfekt all count (Germans speak all three), and so does "
            "the participle used as a Zustandspassiv/adjective (SATZ-024: "
            "\"…dass die Lieferung abgeschlossen ist\" for card 'abschließen' "
            "is word_ok=true — never 'correct' a genuinely correct sentence "
            "like this into \"…abgeschlossen war\" or \"…hat abgeschlossen\", "
            "both of which are WORSE than what the learner said). Using the "
            "verb in the present or future means word_ok=false, even if the "
            "sentence is otherwise fine. A BROKEN past form is ALSO "
            "word_ok=false even though the verb is clearly attempted "
            "(SATZ-020): an auxiliary paired with a FINITE Präteritum instead "
            "of the participle (\"Ich habe das Projekt ihm nicht übergab\" — "
            "should be 'übergeben') is neither Perfekt nor Präteritum — "
            "that's a broken FORM, pattern_id 'partizip2-form'. Do NOT confuse "
            "this with the wrong AUXILIARY paired with the CORRECT participle "
            "(\"Ich habe … beigetreten\" instead of \"Ich bin … beigetreten\") "
            "— the participle itself is fine there, so word_ok stays TRUE; "
            "that is grammar_ok=false, pattern_id 'perfekt-aux-sein', same as "
            "any other wrong-word-elsewhere mistake. 'partizip2-form' is only "
            "for when the participle's own shape is missing or broken, never "
            "'perfekt-satzklammer' (that id is only for a correctly-built "
            "participle sitting in the wrong bracket position)"
        )
        if card.tense_form:
            lines.append(f"- its natural spoken past: {card.tense_form}")
    lines.append(f"- meaning: {card.gloss}")
    if card.note:
        lines.append(f"- grammar note: {card.note}")
    return "\n".join(lines)


# --- TASK 6: programmatic word-presence guard --------------------------
#
# The LLM judge alone would occasionally mark word_ok=true when the target
# word was never actually said (a similar-sounding word, or the model being
# lenient). There was zero programmatic check on this before — this is a
# deterministic, conservative stem scan run BEFORE the LLM call (its result
# is injected into the prompt) and used again AFTER parsing to veto a
# word_ok=true the scan clearly contradicts. "Conservative" means: any
# ambiguity resolves to True (word present) — it only returns False when the
# target is CLEARLY absent from the transcript.

_UMLAUT_TABLE = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize_de(text: str) -> str:
    """casefold + ä/ö/ü→a/o/u, ß→ss + punctuation stripped — a forgiving
    normalization so a stem match survives ASR spelling noise.

    Defined up here (above the constants that follow) because
    ``_SEPARABLE_PREFIXES`` folds itself through it at import time.
    """
    return _PUNCT_RE.sub("", text.casefold().translate(_UMLAUT_TABLE))

# Longest-first so e.g. "zurück" is matched before the shorter "zu" would
# otherwise wrongly match its first two letters.
#
# NOTE the `_normalize_de` wrapper: these are matched against words that have
# already been umlaut-folded, so a prefix spelled with an umlaut here would
# never fire. "zurück" was in this list from the start and had NEVER matched
# anything — `_normalize_de("zurückkommen")` is "zuruckkommen", which does not
# start with "zurück" — so every separated "… kommt … zurück" was scanning as
# "target absent" exactly like the auseinandersetzen case below. Folding the
# list through the same normalizer is what actually makes it work.
#
# SATZ-021 — the long prefixes below were missing, and their absence silently
# failed learners. "auseinandersetzen" matched the shorter "aus", giving the
# nonsense remainder stem "einandersetz"; the real remainder "setz" was never
# a candidate, so a correctly separated "Wir setzen uns … auseinander" scanned
# as "target absent" and the guard OVERRODE a judge that had just called the
# sentence perfect. Seen in production 2026-08-14: one learner hit it three
# times on the same card, the third attempt flawless, and left the drill.
#
# Adding a prefix can only ever ADD stems (see the module note above:
# over-generating turns false negatives into matches and cannot erase the
# "clearly absent" signal), and a longer match displacing a shorter wrong one
# strictly improves the remainder — "setz" instead of "einandersetz".
_SEPARABLE_PREFIXES = sorted(
    {
        _normalize_de(p)
        for p in (
            "ab", "an", "auf", "aus", "bei", "ein", "mit", "nach", "vor", "zu",
            "zurück", "weg", "her", "hin", "um", "los",
            # SATZ-021 additions.
            "auseinander", "zusammen", "entgegen", "gegenüber", "entlang",
            "voraus", "voran", "vorbei", "vorüber", "hervor",
            "heraus", "herein", "herum", "herunter", "hinauf", "hinaus",
            "hinein", "hinunter", "teil", "fest", "fern", "frei", "statt",
            "wieder", "weiter", "fort", "heim", "nieder", "davon", "dabei",
        )
    },
    key=len,
    reverse=True,
)

# Function words that never carry the target's own inflection — dropped
# when picking content words out of a (possibly multi-word) target/tense_form
# like "sich freuen auf". Already-normalized (casefold, umlauts folded).
_SKIP_WORDS = {
    "sich",
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einen", "einem", "einer", "eines",
    "an", "auf", "in", "im", "am", "aus", "bei", "mit", "nach", "seit",
    "von", "vom", "zu", "zum", "zur", "durch", "fur", "gegen", "ohne",
    "um", "uber", "unter", "vor", "hinter", "neben", "zwischen",
    "gegenuber", "wegen", "trotz", "wahrend", "statt", "anstatt",
    "und", "oder",
}


def _stem(word: str) -> str:
    """Word minus its last 2 chars, floored at a 3-char minimum stem — below
    that threshold the whole (short) word IS the stem."""
    return word[:-2] if len(word) - 2 >= 3 else word


def _vowel_variants(stem: str) -> set[str]:
    """SATZ-019: German strong verbs raise their stem vowel in the 2nd/3rd
    person singular present — gelten->gilt, geben->gibt, nehmen->nimmt,
    sehen->sieht, lesen->liest, helfen->hilft, sprechen->spricht, treffen
    ->trifft, essen->isst. A stem built straight off the infinitive
    ("gelt", "geb", "nehm", ...) never prefix-matches the form a learner
    who got it right actually says, so the guard vetoed a correct answer.
    Emit the e->i and e->ie variants of the stem's first "e" (plus the
    eh->i contraction "nehmen" needs, since e->i alone would give "nihm"
    instead of the actual "nimmt" stem) so those forms count as evidence.
    Stems are already normalized (casefold, umlauts folded), so only plain
    "e" ever needs handling here. This only ever ADDS stems to the set the
    caller already has — the guard stays conservative: over-generating
    candidates can only turn a false negative into a match, it can't erase
    the "target clearly absent" signal the guard exists to catch."""
    variants = set()
    if "e" in stem:
        variants.add(stem.replace("e", "i", 1))
        variants.add(stem.replace("e", "ie", 1))
    if "eh" in stem:
        variants.add(stem.replace("eh", "i", 1))
    # A 2-char variant (e.g. "seh" -> "si") would prefix-match half the
    # language, so floor at 3 — same minimum the base stem uses.
    return {v for v in variants if len(v) >= 3 and v != stem}


def _candidate_stems(word: str) -> set[str]:
    """One normalized content word -> its stem(s). A separable-prefix verb
    ("einladen") also contributes the stem of the remainder after the
    prefix ("lad"), so a split spoken form ("... lade ... ein") still
    matches even though the transcript token never contains the full verb.
    Both the base stem and the remainder stem also get their strong-verb
    e->i(e) vowel variants (SATZ-019) — a separable strong verb ("einladen"
    isn't one, but e.g. "aufgeben"'s remainder "geb" is) has the same
    stem-vowel raising in 2nd/3rd person singular."""
    base_stem = _stem(word)
    stems = {base_stem} | _vowel_variants(base_stem)
    for prefix in _SEPARABLE_PREFIXES:
        if word.startswith(prefix) and len(word) > len(prefix):
            remainder = word[len(prefix):]
            if len(remainder) > 3:
                remainder_stem = _stem(remainder)
                stems.add(remainder_stem)
                stems |= _vowel_variants(remainder_stem)
            break  # longest matching prefix only
    return stems


# SATZ-023: an adverb's note may name a genuine, often IRREGULAR comparative
# ("comparison: lieber · am liebsten" for gern, "comparison: öfter · am
# häufigsten" for oft) that shares NO stem with the base word — "lieber"
# doesn't start with any prefix of "gern". The examiner prompt already tells
# the model a comparative counts as using the word (see the adverb block in
# _card_brief), but without this the deterministic scan below still vetoes
# it as "target word not heard", overriding a model that got it right. Same
# class of fix as the tense_form/extra_forms handling just above: teach the
# guard about the sibling form instead of leaving it to prompt compliance.
_ADVERB_COMPARISON_RE = re.compile(r"comparison:\s*(.+)$", re.IGNORECASE)


def _adverb_comparison_forms(note: str | None) -> list[str]:
    """Pull the comparative/superlative word(s) out of an adverb card's
    ``note`` (e.g. "comparison: lieber · am liebsten" -> ["lieber",
    "liebsten"]), or [] when the note states no comparison."""
    if not note:
        return []
    m = _ADVERB_COMPARISON_RE.search(note)
    if not m:
        return []
    forms = []
    for chunk in re.split(r"[·,]", m.group(1)):
        chunk = chunk.strip()
        if chunk[:3].lower() == "am ":
            chunk = chunk[3:].strip()
        if chunk:
            forms.append(chunk)
    return forms


def _target_evidence(card, transcript: str, extra_forms: list[str] | None = None) -> bool:
    """Deterministic, conservative scan: does ANY transcript token plausibly
    carry an inflected or separated form of the card's target word?
    ``extra_forms`` carries sibling spoken forms — e.g. the past sibling's
    tense_form for a base verb card — so strong-verb pasts count as evidence.

    Returns True (word present / stay conservative) when the transcript is
    empty, when no usable stem could be built from the target at all, or
    when a matching token is found. Returns False only when the target has
    usable stems and NONE of them show up anywhere in the transcript —
    the "clearly absent" case this guard exists to catch.
    """
    transcript_tokens = _normalize_de(transcript or "").split()
    if not transcript_tokens:
        return True

    candidate_words = _normalize_de(getattr(card, "target", "") or "").split()
    tense_form = getattr(card, "tense_form", None)
    if tense_form:
        candidate_words += _normalize_de(tense_form).split()
    if getattr(card, "type", None) == "adverb":
        candidate_words += _normalize_de(
            " ".join(_adverb_comparison_forms(getattr(card, "note", None)))
        ).split()
    for form in extra_forms or []:
        candidate_words += _normalize_de(form).split()

    stems: set[str] = set()
    for word in candidate_words:
        if word in _SKIP_WORDS or len(word) <= 3:
            continue
        stems |= _candidate_stems(word)

    if not stems:
        return True

    return any(tok.startswith(stem) for tok in transcript_tokens for stem in stems)


# --- SATZ-024/025/022/020 (2026-08-15): structural axis/veto guards --------
#
# Four independent, conservative post-parse checks run after the LLM call in
# examine_attempt(). Each backs a prompt fix above with code that holds even
# when the model drifts (per CLAUDE.md's judge-prompt convention) — none of
# them replace the model's judgement wholesale, they only correct it in the
# one narrow shape each is aimed at.

# SATZ-024 P3: an article/case/ending fix ON the target got filed as
# word_ok=false by an older prompt that (wrongly) called that word_ok's job.
# If the target is demonstrably present (TASK 6 evidence) and the model's
# own explanation names an article/case/ending problem, the verdict is an
# AXIS mistake, not a "word missing" one — flip it rather than fail the
# learner on the wrong flag.
_AXIS_OVERRIDE_RE = re.compile(
    r"\barticle\b|\bArtikel\b|wrong case|case ending|case error|in the wrong case|"
    r"\bKasus\b|\b(?:accusative|dative|genitive|nominative|Akkusativ|Dativ|Genitiv|Nominativ)\b|"
    r"\bending\b|\bEndung\b|declension|Deklination",
    re.IGNORECASE,
)

# Same class of bug, but real traces (fa7e46d8…, 5445c770…) showed the model
# flips word_ok on an article swap WITHOUT ever populating `error` (the
# prompt allows a null error when "the correction alone reveals why") — the
# regex above never fires on those. An article-only diff between the
# transcript and `corrected` is the article/case fix in exactly that shape,
# so it is checked independently of what `error` says.
_ARTICLE_TOKENS = {
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einen", "einem", "einer", "eines",
}


def _article_only_diff(transcript: str, corrected: str) -> bool:
    """True iff `corrected` differs from `transcript` in exactly one token,
    and that token is an ARTICLE-for-ARTICLE swap (der->den, die->der, …) —
    the shape of a pure article/case fix, as opposed to a lexical change or
    a word-order rewrite. Conservative: a token-count mismatch or any other
    kind of change returns False."""
    t_toks = _normalize_de(transcript or "").split()
    c_toks = _normalize_de(corrected or "").split()
    if len(t_toks) != len(c_toks):
        return False
    diffs = [(a, b) for a, b in zip(t_toks, c_toks) if a != b]
    if len(diffs) != 1:
        return False
    old, new = diffs[0]
    return old in _ARTICLE_TOKENS and new in _ARTICLE_TOKENS


# SATZ-020 P2: the inverse mistake — a BROKEN past form ("habe … übergab")
# gets word_ok=true because a verb was clearly attempted. If the model's own
# `error` names the participle/auxiliary/form AND its `corrected` changed
# the target verb's own token, the flagged error is ON the target's form —
# veto the pass regardless of what word_ok said.
_PAST_FORM_ERROR_RE = re.compile(
    r"participle|partizip|auxiliary|hilfsverb|perfekt|pr[aä]teritum|\bform\b",
    re.IGNORECASE,
)


def _target_token_in(card, text: str) -> str | None:
    """First normalized token in `text` whose stem matches one of the card's
    own target/tense_form stems (reuses `_candidate_stems`, same as the
    TASK 6 evidence scan), or None. Lets the SATZ-020 P2 guard compare the
    target verb's own token across the transcript and `corrected`."""
    candidate_words = _normalize_de(getattr(card, "target", "") or "").split()
    tense_form = getattr(card, "tense_form", None)
    if tense_form:
        candidate_words += _normalize_de(tense_form).split()
    stems: set[str] = set()
    for word in candidate_words:
        if word in _SKIP_WORDS or len(word) <= 3:
            continue
        stems |= _candidate_stems(word)
    if not stems:
        return None
    for tok in _normalize_de(text or "").split():
        if any(tok.startswith(stem) for stem in stems):
            return tok
    return None


# SATZ-022: an orthography-only finding (hyphenation/compound spacing,
# capitalization, punctuation) may never fail a SPOKEN attempt — every
# caller of this examiner (satz/routes.py, verbformen/routes.py) transcribes
# audio first, so there is no typed path to gate this behind; it applies to
# every call unconditionally, matching the module docstring above.
_ORTHOGRAPHY_ONLY_RE = re.compile(
    r"hyphen|Bindestrich|capitali[sz]|Großschreib|Kleinschreib|punctuation|"
    r"Interpunktion|\bKomma\b|spacing|compound|Getrenntschreib|zusammenschreib|"
    r"zusammengeschrieben",
    re.IGNORECASE,
)
# …and the finding must be ONLY orthographic: an error line that also names
# a grammar category (a real ending, case, verb-form or word-order problem
# that happens to mention capitalisation too) must not be waved through.
_GRAMMAR_TERM_RE = re.compile(
    r"\bcase\b|Kasus|Akkusativ|Dativ|accusative|dative|\bending\b|Endung|"
    r"\bverb\b|Verb\b|order|Wortstellung|\barticle\b|Artikel|tense|Zeitform|"
    r"participle|Partizip|auxiliary|plural|Plural|gender|Genus|preposition|Präposition",
    re.IGNORECASE,
)

# SATZ-025 P1: a small, deliberately conservative set of ENGLISH-ONLY
# function/filler words with NO German homograph — a whole-token match is
# the cheap backstop for "part of the sentence isn't German" when the model
# waves a half-English sentence through. Excluded on purpose because they
# ARE valid German words: was, will, so, man, die, das, ist, an, in, um, am,
# im — a false positive here fails a genuinely correct sentence, which is
# worse than missing a genuine one.
_ENGLISH_FRAGMENT_WORDS = {
    "the", "that", "good", "is", "and", "with", "very", "you", "your",
    "have", "has", "this", "these", "those", "because", "but", "really",
    "actually", "please", "thanks", "thank",
}


def _compound_merge_only(transcript: str, corrected: str) -> bool:
    """True iff `corrected` equals `transcript` with exactly one pair of
    adjacent tokens joined into one ("Modelle Ebene" -> "Modellebene") — the
    shape of an ASR compound split (SATZ-022), which is the recognizer's
    spelling, never the learner's error. Case-insensitive, punctuation
    ignored; any other change returns False."""
    t = _normalize_de(transcript or "").split()
    c = _normalize_de(corrected or "").split()
    if len(c) != len(t) - 1 or len(t) < 2:
        return False
    for i in range(len(t) - 1):
        merged = t[:i] + [t[i] + t[i + 1]] + t[i + 2 :]
        if merged == c:
            return True
        # "Modelle Ebene" -> "Modellebene": the first half drops its final
        # -e/-en/-n linking vowel when the compound is written as one word.
        for cut in (1, 2):
            if len(t[i]) > cut + 2:
                merged = t[:i] + [t[i][:-cut] + t[i + 1]] + t[i + 2 :]
                if merged == c:
                    return True
    return False


def _has_english_fragment(transcript: str) -> bool:
    tokens = _normalize_de(transcript or "").split()
    return any(tok in _ENGLISH_FRAGMENT_WORDS for tok in tokens)


async def examine_attempt(
    card,
    transcript: str,
    *,
    span_name: str = "satz-judge",
    extra_forms: list[str] | None = None,
) -> Judgement:
    """One structured-output judgement call over the card + transcript.

    ``span_name`` (TASK 4) lets sibling drills that reuse this examiner
    (e.g. verbformen) file their spans under their own name instead of the
    generic default, so Langfuse traces stop reading as an undifferentiated
    "llm" soup.
    """
    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    llm = structured_judge_llm(Judgement)

    # TASK 6: run the programmatic scan BEFORE the call so its result can be
    # injected into the prompt as a hint (the LLM still makes the final call
    # — this only nudges/vetoes, it doesn't replace the judgement).
    evidence = _target_evidence(card, transcript, extra_forms=extra_forms)
    evidence_line = (
        "Programmatic scan: an inflected form of the target WAS detected in "
        "the transcript."
        if evidence
        else "Programmatic scan: an inflected form of the target was NOT "
        "detected in the transcript. If you still set word_ok=true you must "
        "be certain an inflected or separated form is genuinely present — a "
        "similar-sounding or related word does not count."
    )

    prompt = (
        PROMPT.replace("{card}", _card_brief(card))
        .replace("{transcript}", transcript)
        .replace("{taxonomy}", taxonomy_brief())
        .replace("{evidence_line}", evidence_line)
    )
    # OBS-006/OBS-008: the judge's own generation span — the usual suspect
    # for the slow attempts (OpenRouter queueing / provider fallback), now
    # measured per call with model + tokens + served-model attached.
    with generation_span(span_name, model=EXAMINER_MODEL, input_text=prompt) as span:
        span.set_attribute("word_evidence", evidence)
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)

    if not evidence and result.word_ok:
        # The scan clearly found no trace of the target — override a
        # false-positive word_ok and tell the learner why, as one short
        # clause prepended to whatever feedback the model already wrote.
        result.word_ok = False
        note = "target word not heard"
        result.error = f"{note} — {result.error}" if result.error else note

    # SATZ-024 P3: word_ok=false but the target IS present (evidence=True,
    # so the TASK 6 veto above did NOT fire) and the mistake the model named
    # is an article/case/ending problem — that is a grammar_ok mistake filed
    # on the wrong axis. Flip it rather than fail the learner on "word not
    # used" for a word they demonstrably used.
    # SATZ-027 (2026-08-17), trace b6b668fc2ef7fb801bc2a6351bb4e583: on a
    # `-past` card the override above must not fire when `corrected` also
    # rewrote the target verb's OWN token (verschieben -> verschoben) — that
    # is the model saying the past form was never produced, and the
    # article/case wording it also used names a SECOND, separate problem,
    # not the reason word_ok was false. Reuses the same `_target_token_in`
    # helper the SATZ-020 P2 veto below already relies on.
    past_form_touched = False
    infinitive_only = False
    if getattr(card, "tense", None) == "past" and not result.word_ok and result.corrected:
        orig_tok = _target_token_in(card, transcript)
        fixed_tok = _target_token_in(card, result.corrected)
        past_form_touched = bool(orig_tok and fixed_tok and orig_tok != fixed_tok)
        # SATZ-027 tester case (2026-08-17), "Ich muss das Termin
        # verschieben." -> corrected "Ich musste den Termin verschieben.":
        # the token-diff check above misses this one, because a modal in
        # Präteritum + bare infinitive doesn't touch the main verb's token
        # at all — `corrected` fixes the MODAL (muss -> musste), not
        # `verschieben`. But the card's `target` on a `-past` card IS the
        # bare infinitive, so if that's the exact token the learner said,
        # they never produced a past form of anything — same underlying bug
        # as the token-diff case, caught a different way.
        if (
            not past_form_touched
            and orig_tok
            and orig_tok == _normalize_de(getattr(card, "target", "") or "")
        ):
            infinitive_only = True

    skip_override = past_form_touched or infinitive_only
    _axis_override_matches = not result.word_ok and evidence and (
        _AXIS_OVERRIDE_RE.search(f"{result.error or ''} {result.corrected or ''}")
        or (result.corrected and _article_only_diff(transcript, result.corrected))
    )
    if _axis_override_matches and not skip_override:
        logger.info(
            "satz.axis_override: word_ok false->true, grammar_ok forced false "
            "(card {}, {!r} -> {!r})",
            getattr(card, "id", "?"), transcript, result.corrected,
        )
        result.word_ok = True
        result.grammar_ok = False
        if not result.error:
            result.error = "article/case on the target word"
    elif _axis_override_matches and infinitive_only:
        logger.info(
            "satz.axis_override_skipped: infinitive, no past form (card {})",
            getattr(card, "id", "?"),
        )
    elif _axis_override_matches and skip_override:
        logger.info(
            "satz.axis_override_skipped: past form changed {!r}->{!r} (card {})",
            _target_token_in(card, transcript), _target_token_in(card, result.corrected),
            getattr(card, "id", "?"),
        )

    # SATZ-020 P2: for a `-past` card, word_ok=true but the model's own
    # `error` names the participle/auxiliary/form AND `corrected` changed
    # the target verb's own token — the flagged mistake is ON the target's
    # form, so the past-tense card must not pass regardless of what the
    # model's word_ok said.
    if (
        getattr(card, "tense", None) == "past"
        and result.word_ok
        and result.corrected
        and _PAST_FORM_ERROR_RE.search(result.error or "")
    ):
        orig_tok = _target_token_in(card, transcript)
        fixed_tok = _target_token_in(card, result.corrected)
        if orig_tok and fixed_tok and orig_tok != fixed_tok:
            logger.info(
                "satz.past_form_veto: word_ok true->false (card {}, {!r} -> {!r})",
                getattr(card, "id", "?"), orig_tok, fixed_tok,
            )
            result.word_ok = False

    # SATZ-025 P1: both axes passed, but the transcript contains a whole
    # ENGLISH-ONLY token with no German homograph — the sentence isn't (all)
    # German. Backstop for when the model waves a half-English attempt
    # through; see the plausibility-gate prompt rules above for the primary
    # defense.
    if result.word_ok and result.grammar_ok and _has_english_fragment(transcript):
        logger.info(
            "satz.english_fragment_override: grammar_ok true->false (card {}, {!r})",
            getattr(card, "id", "?"), transcript,
        )
        result.grammar_ok = False
        result.error = "part of the sentence is not German"

    # SATZ-022: an orthography-only finding may never fail a spoken attempt
    # — every caller here transcribes audio first (see module docstring), so
    # this always applies, no `spoken` flag needed. Overrides whichever axis
    # the model failed on.
    if not (result.word_ok and result.grammar_ok) and (
        (
            result.error
            and _ORTHOGRAPHY_ONLY_RE.search(result.error)
            and not _GRAMMAR_TERM_RE.search(result.error)
        )
        or (result.corrected and _compound_merge_only(transcript, result.corrected))
    ):
        logger.info(
            "satz.orthography_override: forcing word_ok/grammar_ok true "
            "(card {}, error {!r})",
            getattr(card, "id", "?"), result.error,
        )
        result.word_ok = True
        result.grammar_ok = True
        result.error = None
        result.corrected = None
        result.pattern_id = None

    if result.word_ok and result.grammar_ok:
        # A "fix" on a fully correct sentence only confuses.
        result.corrected = None
        result.error = None
        result.pattern_id = None
    if result.pattern_id and result.pattern_id not in load_taxonomy():
        # The ledger keys on catalog ids — a hallucinated slug must not enter.
        result.pattern_id = None
    return result
