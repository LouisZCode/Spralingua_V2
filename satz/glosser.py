"""On-demand word gloss for the hover/tap-to-translate UI (UI-007).

Hover/tap any German word anywhere in the app -> translation + example, with
click-to-add into the vocabulary deck (the add itself reuses ``POST
/satz/cards`` — this module only builds the lookup). Same Cerebras
structured-output wiring as ``satz/explainer.py``: one structured-output
call, no streaming.

The route (``satz/routes.py::gloss_word_route``) checks the closed-class
function-word table below (``function_word_gloss``), then the shared
Satzschmiede catalog, then the ``word_glosses`` cache table before ever
reaching an LLM call — this module's LLM path (``gloss_word``) only runs on
a genuine miss through all three, and its result gets cached so the same
surface form never triggers a second call.

SATZ-026 (2026-08-15 trace review): 2 of 39 hover lookups in 24h returned a
wrong lemma, and both were function words — "Die" (article) glossed as the
noun it precedes ("Schuh", trace ``af66e7f116ef011c850ae9c9fdbfcbb7``), and
"stelle" (separable verb) glossed as the unrelated noun "Stelle" instead of
"vorstellen" (trace ``d0017329f2d96cad5b53e0e734880904``). Articles and
pronouns are a small, closed set — glossing them from a table is strictly
cheaper AND cannot drift the way an LLM guess can, which matters because a
wrong gloss becomes a wrong permanent vocab card the moment the learner taps
"add to deck" (SATZ-013). See ``function_word_gloss`` and
``_separable_prefix_hint`` below.
"""

import re
from typing import Optional

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from satz.examiner import _SEPARABLE_PREFIXES, _normalize_de

GLOSSER_MODEL = "openai/gpt-oss-120b"


class WordGlossResult(BaseModel):
    """One dictionary-form gloss for a word as used in a given sentence."""

    lemma: str = Field(
        description=(
            "Dictionary form: noun -> nominative singular (bare, no "
            "article); adjective -> base (positive) form; verb (incl. "
            "participles/finite forms) -> infinitive"
        )
    )
    article: Optional[str] = Field(
        default=None,
        description="der/die/das if lemma is a noun, else null",
    )
    gloss: str = Field(
        description=(
            "Concise English meaning of the lemma AS USED in the given "
            "sentence, 2-5 words, no sentence"
        )
    )
    example: str = Field(
        description=(
            "One NEW short A1/A2 German sentence using the lemma "
            "naturally — not the given sentence"
        )
    )


# ---------------------------------------------------------------------------
# SATZ-026 layer 1: deterministic function-word gloss — no LLM call.
# ---------------------------------------------------------------------------
# Articles, personal/reflexive/possessive pronouns, and the commonest
# particles never need the model: the correct gloss is a fixed lookup, and
# a table hit is marked `glossable=False` by the route (satz/routes.py) so
# "add to deck" is never offered for one of these — the exact failure mode
# in the "Die" -> "Schuh" trace, where a wrong LLM gloss of a function word
# would otherwise be one tap from becoming a permanent bad vocab card.

_ARTICLE_GLOSSES: dict[str, str] = {
    "der": "the (definite article — masc. nominative; also fem./plural genitive or dative; can also be a relative pronoun 'who/which/that')",
    "die": "the (definite article — feminine singular, or any-gender plural; can also be a relative pronoun 'who/which/that')",
    "das": "the (definite article — neuter nominative/accusative; can also be a relative pronoun 'which/that')",
    "den": "the (definite article — masc. accusative singular, or dative plural; can also be a relative pronoun 'who/which/that')",
    "dem": "the (definite article — masc./neuter dative singular; can also be a relative pronoun 'who/which/that')",
    "des": "the (definite article — masc./neuter genitive singular)",
    "ein": "a / an (indefinite article — masc./neuter nominative, or neuter accusative) — or a separable verb prefix (e.g. einsteigen 'to get in') when it stands apart, at the end of a clause, from a verb",
    "eine": "a / an (indefinite article — feminine nominative/accusative)",
    "einen": "a / an (indefinite article — masculine accusative)",
    "einem": "a / an (indefinite article — masc./neuter dative)",
    "einer": "a / an (indefinite article — feminine dative/genitive, or masc./neuter genitive)",
    "eines": "a / an (indefinite article — masc./neuter genitive)",
    "kein": "no / not a (negating article — masc./neuter nominative, or neuter accusative)",
    "keine": "no / not a (negating article — feminine, or any-gender plural)",
    "keinen": "no / not a (negating article — masculine accusative)",
    "keinem": "no / not a (negating article — masc./neuter dative)",
    "keiner": "no / not a (negating article — feminine dative/genitive, or plural genitive)",
    "keines": "no / not a (negating article — masc./neuter genitive)",
}
_ARTICLE_EXAMPLES: dict[str, str] = {
    "der": "Der Mann liest ein Buch.",
    "die": "Die Frau liest ein Buch.",
    "das": "Das Kind liest ein Buch.",
    "den": "Ich sehe den Mann.",
    "dem": "Ich helfe dem Mann.",
    "des": "Das Auto des Mannes ist rot.",
    "ein": "Das ist ein Buch.",
    "eine": "Das ist eine Tasche.",
    "einen": "Ich sehe einen Hund.",
    "einem": "Ich helfe einem Freund.",
    "einer": "Ich helfe einer Freundin.",
    "eines": "Das Auto eines Freundes ist rot.",
    "kein": "Das ist kein Problem.",
    "keine": "Das ist keine gute Idee.",
    "keinen": "Ich habe keinen Hunger.",
    "keinem": "Ich glaube keinem Wort.",
    "keiner": "Ich glaube keiner Ausrede.",
    "keines": "Der Preis keines Buches war hoch.",
}

_PRONOUN_GLOSSES: dict[str, str] = {
    "ich": "I (nominative)",
    "du": "you (informal singular, nominative)",
    "er": "he / it (nominative, masculine)",
    "sie": "she / it (nominative or accusative, feminine singular) — or they (nominative/accusative plural)",
    "es": "it (nominative or accusative, neuter)",
    "wir": "we (nominative)",
    "mich": "me (accusative)",
    "dich": "you (informal singular, accusative)",
    "ihn": "him / it (accusative, masculine)",
    "sich": "himself / herself / itself / themselves / yourself (formal) — reflexive pronoun",
    "uns": "us (accusative or dative)",
    "euch": "you all (informal plural, accusative or dative)",
    "mir": "to/for me (dative)",
    "dir": "to/for you (informal singular, dative)",
    "ihm": "to/for him / it (dative)",
    "ihnen": "to/for them (dative)",
}
_PRONOUN_EXAMPLES: dict[str, str] = {
    "ich": "Ich wohne in Berlin.",
    "du": "Du bist mein Freund.",
    "er": "Er kommt aus Spanien.",
    "sie": "Sie kommt aus Frankreich.",
    "es": "Es regnet heute.",
    "wir": "Wir gehen ins Kino.",
    "mich": "Er sieht mich.",
    "dich": "Ich sehe dich.",
    "ihn": "Sie kennt ihn gut.",
    "sich": "Er freut sich auf die Ferien.",
    "uns": "Sie helfen uns.",
    "euch": "Ich rufe euch morgen an.",
    "mir": "Gib mir das Buch.",
    "dir": "Ich gebe dir das Buch.",
    "ihm": "Ich helfe ihm.",
    "ihnen": "Ich schreibe ihnen einen Brief.",
}

# "ihr" alone is genuinely three-way ambiguous even with the sentence in
# hand — nominative "you all", dative "to/for her", or (directly before a
# noun) possessive "her/their". Honest here means listing every reading,
# not picking one.
_IHR_COMBINED_GLOSS = (
    "you all (informal plural, nominative) — or to/for her (dative) — or "
    "her/their (possessive, directly before a noun)"
)
_IHR_EXAMPLE = "Ihr kommt heute, und ich helfe ihr mit ihrem Koffer."
# The possessive-ending forms of "ihr" ("her/their X") — not covered by the
# pronoun table above (only the bare form is a pronoun) and not generated
# by the possessive-stem loop below (that loop deliberately excludes "ihr"
# as a stem so it doesn't collide with the three-way gloss above).
_IHR_POSSESSIVE_GLOSSES: dict[str, str] = {
    "ihre": "her/their (possessive determiner, directly before a noun)",
    "ihren": "her/their (possessive determiner, directly before a noun)",
    "ihrem": "her/their (possessive determiner, directly before a noun)",
    "ihrer": "her/their (possessive determiner, directly before a noun)",
    "ihres": "her/their (possessive determiner, directly before a noun)",
}
_IHR_POSSESSIVE_EXAMPLES: dict[str, str] = {
    "ihre": "Das ist ihre Tasche.",
    "ihren": "Ich sehe ihren Hund.",
    "ihrem": "Ich helfe ihrem Vater.",
    "ihrer": "Ich helfe ihrer Mutter.",
    "ihres": "Das Auto ihres Vaters ist rot.",
}

# "Sie"/"Ihnen"/"Ihr"-family formal-address forms only ever capitalize
# mid-sentence — a capital hit at the very START of a sentence is ambiguous
# with ordinary sentence-initial capitalization of the lowercase word, so
# that position gets both readings (see `_is_sentence_initial`); anywhere
# else, capitalization is unambiguous.
_FORMAL_GLOSSES: dict[str, str] = {
    "sie": "you (formal, nominative or accusative)",
    "ihnen": "to/for you (formal, dative)",
    "ihr": "your (formal, possessive, directly before a noun)",
    "ihre": "your (formal, possessive)",
    "ihren": "your (formal, possessive)",
    "ihrem": "your (formal, possessive)",
    "ihrer": "your (formal, possessive)",
    "ihres": "your (formal, possessive)",
}
_FORMAL_EXAMPLES: dict[str, str] = {
    "sie": "Kommen Sie bitte herein.",
    "ihnen": "Ich danke Ihnen sehr.",
    "ihr": "Ist das Ihr Auto?",
    "ihre": "Ist das Ihre Tasche?",
    "ihren": "Ich habe Ihren Brief bekommen.",
    "ihrem": "Ich helfe Ihrem Kollegen.",
    "ihrer": "Ich helfe Ihrer Kollegin.",
    "ihres": "Der Titel Ihres Buches gefällt mir.",
}

# Possessive determiners. "sein" (bare, no ending) is deliberately left OUT
# of the generated table below — unlike every other bare stem here, "sein"
# is also the infinitive of the verb "to be" ("Er will glücklich sein"), so
# a confident table hit there would be exactly the kind of wrong-but-sure
# guess this layer exists to avoid; every ENDED form (seine/seinen/...) is
# unambiguous and stays in.
_POSSESSIVE_STEMS: dict[str, str] = {
    "mein": "my",
    "dein": "your (informal singular)",
    "sein": "his / its",
    "unser": "our",
    "euer": "your (informal plural)",
}
# (ending, example sentence template) per case slot, reused across stems.
_POSSESSIVE_SLOTS: list[tuple[str, str]] = [
    ("", "Das ist {poss} Buch."),
    ("e", "Das ist {poss} Tasche."),
    ("en", "Ich sehe {poss} Hund."),
    ("em", "Ich helfe {poss} Vater."),
    ("er", "Ich helfe {poss} Mutter."),
    ("es", "Das Auto {poss} Vaters ist rot."),
]


def _build_possessive_tables() -> tuple[dict[str, str], dict[str, str]]:
    glosses: dict[str, str] = {}
    examples: dict[str, str] = {}
    for stem, meaning in _POSSESSIVE_STEMS.items():
        for ending, template in _POSSESSIVE_SLOTS:
            if stem == "sein" and not ending:
                continue  # collides with the verb "sein" — see comment above
            # "euer" drops its own middle e before a vowel-initial ending
            # (eure, not euere) — the one irregular stem in this set.
            form = ("eur" + ending) if (stem == "euer" and ending) else stem + ending
            glosses[form] = f"{meaning} (possessive determiner)"
            examples[form] = template.format(poss=form)
    return glosses, examples


_POSSESSIVE_GLOSSES, _POSSESSIVE_EXAMPLES = _build_possessive_tables()

_FUNCTION_WORD_GLOSSES: dict[str, str] = {
    "und": "and",
    "oder": "or",
    "aber": "but",
    "nicht": "not",
    "auch": "also / too",
    "schon": "already",
    "noch": "still / yet",
    "zu": "to (direction/preposition) / too (degree, e.g. 'zu teuer') / the infinitive marker before a verb — or a separable verb prefix (e.g. zumachen 'to close') when it stands apart, at the end of a clause, from a verb",
    "sondern": "but rather (after a negation)",
    "denn": "because / for — or a flavoring particle in questions ('why then')",
    "doch": "yet / but / still — or an emphatic flavoring particle",
    "sehr": "very",
    "nur": "only",
    "dann": "then",
    "so": "so / like this / that",
}
_FUNCTION_WORD_EXAMPLES: dict[str, str] = {
    "und": "Ich trinke Kaffee und esse ein Brötchen.",
    "oder": "Möchtest du Tee oder Kaffee?",
    "aber": "Ich bin müde, aber ich arbeite weiter.",
    "nicht": "Das ist nicht richtig.",
    "auch": "Ich komme auch mit.",
    "schon": "Ich bin schon fertig.",
    "noch": "Ich bin noch nicht fertig.",
    "zu": "Ich gehe zu meiner Freundin.",
    "sondern": "Das ist nicht rot, sondern blau.",
    "denn": "Ich bleibe zu Hause, denn es regnet.",
    "doch": "Komm doch mit!",
    "sehr": "Das Essen ist sehr gut.",
    "nur": "Ich habe nur zehn Euro.",
    "dann": "Wir essen, und dann gehen wir.",
    "so": "Mach es so, bitte.",
}

_LOWER_GLOSSES: dict[str, str] = {
    **_ARTICLE_GLOSSES,
    **_PRONOUN_GLOSSES,
    **_FUNCTION_WORD_GLOSSES,
    **_POSSESSIVE_GLOSSES,
    **_IHR_POSSESSIVE_GLOSSES,
    "ihr": _IHR_COMBINED_GLOSS,  # overrides the plain pronoun-only entry
}
_LOWER_EXAMPLES: dict[str, str] = {
    **_ARTICLE_EXAMPLES,
    **_PRONOUN_EXAMPLES,
    **_FUNCTION_WORD_EXAMPLES,
    **_POSSESSIVE_EXAMPLES,
    **_IHR_POSSESSIVE_EXAMPLES,
    "ihr": _IHR_EXAMPLE,
}

_EDGE_QUOTES = "\"'()“„»«"


def _is_sentence_initial(word: str, context: str) -> bool:
    """True when `word` is, as far as a cheap check can tell, the first
    word of `context` — the one position where capitalization alone can't
    distinguish formal Sie/Ihr from ordinary sentence-initial capitalization
    of the lowercase word."""
    stripped = context.strip().lstrip(_EDGE_QUOTES)
    return stripped[: len(word)] == word


def function_word_gloss(word: str, context: str) -> Optional["WordGlossResult"]:
    """Deterministic gloss for a closed class of function words — articles,
    personal/reflexive/possessive pronouns, and the commonest particles
    (SATZ-026). Returns ``None`` for anything not in the table, in which
    case the caller falls through to the LLM exactly as before.
    """
    bare = word.strip()
    if not bare:
        return None
    lower = bare.lower()

    # A table word that is ALSO a separable prefix ("zu", "ein", "an" …) and
    # stands stranded at the end of the clause ("Ich höre zu") is the verb's
    # prefix, not the particle — hand it to the LLM path, whose stranded-
    # prefix hint names the verb (zuhören) instead of glossing "to/too".
    if _normalize_de(lower) in _SEPARABLE_PREFIXES:
        tokens = _WORD_RE.findall(context or "")
        if len(tokens) >= 2 and _normalize_de(tokens[-1]) == _normalize_de(lower):
            return None

    if bare[:1].isupper() and lower in _FORMAL_GLOSSES:
        if _is_sentence_initial(bare, context):
            informal = _LOWER_GLOSSES.get(lower, "")
            gloss_text = _FORMAL_GLOSSES[lower]
            if informal:
                gloss_text += (
                    " — or, if this is simply the capitalized start of the "
                    f"sentence rather than formal address: {informal}"
                )
        else:
            gloss_text = _FORMAL_GLOSSES[lower]
        example = _FORMAL_EXAMPLES.get(lower, "")
    elif lower in _LOWER_GLOSSES:
        gloss_text = _LOWER_GLOSSES[lower]
        example = _LOWER_EXAMPLES.get(lower, "")
    else:
        return None

    return WordGlossResult(lemma=lower, article=None, gloss=gloss_text, example=example)


# ---------------------------------------------------------------------------
# SATZ-026 layer 2: separable-verb hint for the LLM path.
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+(?:-[A-Za-zÄÖÜäöüß]+)*")


def _separable_prefix_hint(word: str, context: str) -> Optional[str]:
    """Cheap structural pointer injected into the prompt, not a verdict —
    reuses ``satz/examiner.py``'s SATZ-021 separable-prefix list so "stelle
    … vor" and "vorstellen" agree everywhere in the codebase.

    Flags a token as a possible stranded prefix only when it is the LAST
    word token of the sentence, the normal position for a separated prefix
    in a main clause. A prefix-shaped word followed by more words — "auf
    den Tisch" — is far more likely a preposition governing that noun
    phrase, so it's left alone; the prompt's own control example (below)
    covers that case instead of the code guessing wrong.
    """
    tokens = _WORD_RE.findall(context)
    if not tokens:
        return None
    normalized = [_normalize_de(t) for t in tokens]
    word_norm = _normalize_de(word)
    last = normalized[-1]

    if word_norm == last and word_norm in _SEPARABLE_PREFIXES:
        # The hovered word itself is the stranded prefix — point at the
        # (unnamed) earlier verb instead of guessing which one it is.
        return (
            "The hovered word itself may be a separable verb prefix "
            "stranded at the end of the sentence — check whether an "
            "earlier verb in the sentence pairs with it into one "
            'separable verb, e.g. "stellen" + "vor" -> "vorstellen".'
        )
    if last in _SEPARABLE_PREFIXES and word_norm != last:
        return (
            f'A separable prefix ("{tokens[-1]}") appears later in the '
            "sentence, stranded at the end (not followed by an article or "
            "noun) — if it belongs to the hovered verb, the lemma is the "
            "combined separable verb, e.g. \"stellen\" + \"vor\" -> "
            '"vorstellen", not the bare stem.'
        )
    return None


PROMPT = """# Role
You are a German dictionary lookup inside a language-learning app. The learner hovered or tapped ONE word inside a German sentence. Identify that word's dictionary form (lemma) and gloss it AS USED in that sentence.

# Rules
- Return the DICTIONARY FORM (lemma), not the inflected surface form:
  - noun -> nominative singular, bare (no article in `lemma` itself — put the article in `article`)
  - adjective -> base (positive) form
  - verb, including participles and any finite/conjugated form -> infinitive
- `article`: der/die/das when the lemma is a noun; null for every other word type.
- `gloss`: the concise English meaning of the lemma as it is used HERE, 2-5 words, no full sentence. Gloss the LEMMA itself (singular for nouns, infinitive for verbs), never the inflected surface form.
- `example`: one NEW short A1/A2-level German sentence using the lemma naturally. Do not reuse the given sentence.
- Gloss the HOVERED WORD ITSELF — never a different word nearby it. An article's own gloss is the article, not the noun it precedes; a verb's own gloss is that verb, not a noun that happens to share its stem.
- If the hovered word is a verb form, check whether a separable prefix belongs to it elsewhere in the sentence (a hint may be given below) — if so, the lemma is the COMBINED separable verb, not the bare stem.

# Examples
Sentence: "In diesen Mänteln ist es schön warm."
Word: "Mänteln" -> lemma "Mantel", article "der", gloss "coat", example "Der Mantel hängt an der Tür."

Sentence: "Er wurde von den anderen Kindern gehänselt."
Word: "gehänselt" -> lemma "hänseln", article null, gloss "to tease, to mock", example "Die Kinder hänseln ihn oft."

Sentence: "An einem kalten Tag bleiben wir lieber zu Hause."
Word: "kalten" -> lemma "kalt", article null, gloss "cold", example "Heute ist es sehr kalt."

Sentence: "Die Schuhe passen."
Word: "Die" -> lemma "die", article null, gloss "the (definite article — feminine or plural)", example "Die Katze schläft." — NOT "Schuh": "Die" is the article, never the noun it precedes.

Sentence: "ich stelle mich vor"
Word: "stelle" -> lemma "vorstellen", article null, gloss "to introduce (oneself)", example "Ich stelle meinen Kollegen vor." — the stranded "vor" at the end of the sentence belongs to this verb; the lemma is the combined separable verb, not "stellen" and not the noun "Stelle".

Sentence: "ich rufe dich morgen an"
Word: "rufe" -> lemma "anrufen", article null, gloss "to call (by phone)", example "Ich rufe dich später an." — same pattern: the stranded "an" at the end belongs to the hovered verb.

Sentence: "ich stelle die Vase auf den Tisch"
Word: "stelle" -> lemma "stellen", article null, gloss "to put, to place", example "Ich stelle das Glas auf den Tisch." — CONTROL: "auf" here is a preposition governing "den Tisch" (a full noun phrase follows it), not a stranded separable prefix, so the lemma stays the plain verb.

# Input
Sentence: "{context}"
Word: "{word}"
{hint}"""


async def gloss_word(
    word: str,
    context: str,
    user_id: str | None = None,
    *,
    session_id: str | None = None,
) -> WordGlossResult:
    """One structured-output call: resolve `word`'s lemma + gloss + example
    as used inside `context`.

    ``session_id``, when set, stamps ``langfuse.session.id`` on the
    ``satz-gloss`` span so a hover fired mid-drill files into that
    practice-sitting's Langfuse session instead of standing alone.

    SATZ-026: checks the deterministic function-word table first (defense
    in depth — ``gloss_word_route`` already checks it before ever calling
    this function, but a direct caller, e.g. the sim harness, gets the same
    guarantee this way).
    """
    deterministic = function_word_gloss(word, context)
    if deterministic is not None:
        return deterministic

    # Cerebras-direct primary + OpenRouter fallback with 12s/leg deadline —
    # see agents/openrouter_llm.structured_judge_llm.
    llm = structured_judge_llm(WordGlossResult)
    hint = _separable_prefix_hint(word, context)
    prompt = (
        PROMPT.replace("{context}", context)
        .replace("{word}", word)
        .replace("{hint}", f'Hint: {hint}\n' if hint else "")
    )
    with generation_span(
        "satz-gloss",
        model=GLOSSER_MODEL,
        input_text=prompt,
        user_id=user_id,
        session_id=session_id,
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(
            await llm.ainvoke(prompt)
        )
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result
