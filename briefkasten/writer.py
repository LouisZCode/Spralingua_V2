"""The incoming letter — written per request, never stored (Briefkasten).

``briefkasten/seeds.yaml`` holds situations; this module turns one into an
actual German letter at request time. Two reasons it is generated rather than
canned: the letter can carry the learner's OWN deck words (CONT-002, the same
argument the Bauteil/Verbindungen forges won on), and a seed the learner has
already answered still reads fresh the second time.

Same Cerebras ``gpt-oss-120b`` structured-output wiring as every other judge
in the repo — via ``agents/openrouter_llm.py::structured_judge_llm``, never a
hand-built ChatOpenAI.
"""

import re

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from briefkasten.content import word_target

WRITER_MODEL = "openai/gpt-oss-120b"

# Enough deck words that a few will genuinely fit the situation, few enough
# that the letter never reads like a vocabulary list wearing a disguise. The
# tandem's own vocab layer learned this the hard way at limit=7 (TAND-010):
# an instruction to use EVERY word beat listening, and the partner crammed.
# Hence "where they genuinely fit" and no completeness demand in the prompt.
VOCAB_LIMIT = 6


class LetterDraft(BaseModel):
    """One incoming letter, ready to render."""

    betreff: str = Field(description="A short, natural German subject line")
    body: str = Field(
        description=(
            "The complete German letter: greeting line, the message in "
            "paragraphs, closing line, sender's name. Plain text with real "
            "line breaks, no markdown, no commentary"
        )
    )


PROMPT = """# Role
You write ONE German letter. A learner of German will read it and write a reply — that reply is the exercise. You are writing only the incoming letter.

# Who is writing
Name: {sender_name}
Who they are to the reader: {sender_relation}

# The situation
{situation}

# Register — this is not decoration, get it exactly right
Register: {register}
- `informal` — you and the reader are friends. Use "du" throughout. Everyday German, contractions, the warmth of a real friend writing. Greeting "Hallo {greeting_name}," (use "Liebe/Lieber {greeting_name}," only when the name's gender is unmistakable — "Liebe" before a man's name is a real error), closing like "Viele Grüße", "Liebe Grüße", "Bis bald".
- `formal` — this is a business or official letter. Use "Sie" throughout. Real German Behörden/Geschäft register: complete sentences, no chattiness, no exclamation marks. Greeting "Sehr geehrte Frau …" / "Sehr geehrter Herr …", closing "Mit freundlichen Grüßen".
Never mix the two. A single "du" in a formal letter, or "Sehr geehrte" in a friendly one, ruins the exercise — the learner is being graded on matching your register.
Sign the way the register signs: a formal letter signs a plausible full name (given name + surname, e.g. "Klaus Wagner" — invent a natural given name if the seed gives only a surname), never "Herr X" or "Frau X" — that is how OTHERS address a German, not how a German signs their own letter. An informal letter between friends signs the given name ALONE ("Katrin", never "Katrin Müller" — a friend signing their surname reads like a stranger). Write as one consistent person throughout — "ich" — unless the situation is explicitly from a company or office; don't drift into an unexplained "wir".

# Level and length
The reader is at level {level}. Write at that level — vocabulary and sentence structures they can actually read. Aim for about {min_words}-{max_words} words.

# Who you are writing to
{reader}

# What the letter must do
You are {sender_name}, writing the FIRST letter of an exchange. The learner writes the SECOND letter — their reply. The constraint list below describes THEIR letter, never yours.

The most common failure: writing the learner's reply under the sender's name — confirming receipt of your own letter, saying whether an appointment suits you that you yourself arranged, proposing alternative dates you asked the reader for, asking a question the list assigns to the reader. The same failure wears other coats: asking the reader something whose answer the situation gives only to YOU (if you are the one who moved to the new city, the reader cannot know the cafés there); presupposing a fact the situation contradicts (you moved, not the reader; you are the one traveling, not them); or closing with a wish that belongs in the reader's reply (when YOU are the one leaving, the good-trip wish is theirs to make, not yours). Before you return the letter, reread every sentence and ask WHO in this exchange should be saying it, and whether the situation actually supports it; if the answer is the reader, delete or invert it.

- Cover the situation above naturally — as a person writing, not as a summary of it.
- Embed 2-3 real questions you genuinely ask, so the learner has something to answer. Every question you ask must be one your role in the situation would actually ask — a host never asks a guest what the host should bring to his own party, a landlord doesn't ask the tenant when the landlord is free.

The points below constrain what THEIR reply must do — never something you say or ask yourself:
{points}

## Failure shapes seen in production
- jonas-geburtstag (ASK leak): "Wer ist noch dabei?" — the letter asking the reader's own assigned question.
- nachbarin-pflanzen (decided-fact leak): "Den Schlüssel habe ich beim Hausmeister hinterlegt" — the answer to an ASK point, copied straight from the situation notes.
- vermieter-termin (role inversion): "Der Termin passt mir gut, ich werde zu Hause sein" — the TENANT's line, written into the LANDLORD's letter.

A correct letter makes its own requests, asks its own 2-3 role-coherent questions, and leaves every ASK/PROPOSE slot genuinely open.

# The learner's own vocabulary
{vocab}

# Your German
The learner treats this letter as model German, so it must be flawless, natural German. Reread it once as a proofread before returning — check especially sein-vs-haben perfect auxiliaries, preposition+case, and that every clause is complete. If a sentence feels bent, rewrite it simply.

# Output discipline
`body` is the letter itself and nothing else — no preamble, no "here is the letter", no notes, no markdown, no square-bracket placeholders. Start at the greeting, end after the sender's name. Write only German in both fields.
"""

_VOCAB_NONE = "(none — write the letter normally, this section does not apply)"

# With a first name we can greet the way a real informal letter does. A FORMAL
# letter can't use it: German pairs "Herr"/"Frau" with a SURNAME, and "Sehr
# geehrter Herr Luis" is simply wrong — so formal letters greet the anonymous
# way regardless. Without any name (the demo user, or a Google profile with
# none) the greeting must not need one — "Hallo du," is not something any
# German has ever written.
_READER_NAMED = (
    'The reader is called {name}. Greet them with that name, the way this '
    "sender naturally would."
)
_READER_ANON_INFORMAL = (
    "You do not know the reader's name, so use a greeting that does not need "
    'one — "Hallo!" or similar. Never invent a name and never write a placeholder.'
)
_READER_ANON_FORMAL = (
    "You do not know the reader's surname. Open with "
    '"Sehr geehrte Damen und Herren," — NEVER pair "Herr" or "Frau" with a '
    "first name, that is wrong in German. Never invent a surname."
)

_VOCAB_SOME = """These are German words the reader is currently learning. This is NOT a quota — using ZERO of them is the normal, expected outcome, especially at A1/A2. Use at most ONE, and only if it drops into a sentence so naturally that a reader would never guess it came from a list. Never force one in, never explain or translate one, never draw attention to one. Never bend a sentence to fit one; that is exactly what produced unnatural, calqued lines like "Ich erkenne, dass du gern dabei bist" and "Die Pflanzen sind nicht unberechenbar" in letters where the words simply did not belong. The English gloss only tells you which sense is being learned:
{words}"""


def _reader_line(register: str, reader_name: str | None) -> str:
    """How the sender should greet the learner, given what we know of them."""
    if register == "formal":
        return _READER_ANON_FORMAL
    if reader_name:
        return _READER_NAMED.replace("{name}", reader_name)
    return _READER_ANON_INFORMAL


def _greeting_name(reader_name: str | None) -> str:
    """The bare name for the register section's inline greeting EXAMPLES
    ("Hallo {greeting_name},") — deliberately a separate token from
    `{reader}`, which carries the full instructional blurb for the "Who you
    are writing to" section. Reusing `{reader}` there too was the bug: it
    substituted that whole blurb into the quoted example. Anonymous readers
    get a neutral ellipsis so the example still reads as a greeting shape."""
    return reader_name if reader_name else "…"


def _point_rule(point: str) -> str:
    """Classify one seed point and render its rule text.

    Round-2 judging found the model cannot reliably sort SAY vs. ASK vs.
    PROPOSE itself — worst case, both formal-letter runs came back INVERTED,
    the sender's letter executing the points AS the learner's reply. So
    classification moved out of the model and into code: every point across
    all 10 seeds starts with one of a small set of imperatives (Ask, Suggest,
    Propose, or a plain statement verb like Say/Tell/Thank/Confirm/Explain),
    which is enough to classify deterministically by the leading word."""
    lowered = point.strip().lower()
    if lowered.startswith("ask"):
        return (
            f'- ASK — the learner will ask you this: "{point}". FORBIDDEN: '
            "your letter asking this itself, and any statement or "
            "implication of its answer — even if the situation notes "
            "mention it (keys, names, who's coming, amounts: leave them "
            "out). Their asking is the exercise."
        )
    if lowered.startswith("suggest") or lowered.startswith("propose"):
        return (
            f'- PROPOSE — the learner will do this: "{point}". You may '
            "express a wish or openness, but make NO concrete proposal of "
            "your own — no dates, no times, no plans. The learner "
            "proposes; you would react only in a next letter you are not "
            "writing."
        )
    return (
        f'- SAY — the learner will do this in their reply: "{point}". Make '
        'it natural for them (an invitation is what makes "can you come" '
        "answerable) — and never perform it for them in your own voice."
    )


def _render_vocab(vocab_words: list[dict]) -> str:
    """The deck layer, or an explicit no-op for the first-session case."""
    if not vocab_words:
        return _VOCAB_NONE
    lines = "\n".join(
        f"- {w['word']} — {w['gloss']}" if w.get("gloss") else f"- {w['word']}"
        for w in vocab_words
    )
    return _VOCAB_SOME.replace("{words}", lines)


async def write_letter(
    seed: dict,
    vocab_words: list[dict],
    reader_name: str | None = None,
    user_id: str | None = None,
) -> LetterDraft:
    """Write the incoming letter for one seed.

    ``reader_name`` is the learner's own first name (from their Google
    profile) so the greeting reads like a real letter. ``None`` — the demo
    user, or a profile without a name — falls back to a greeting that doesn't
    need one.
    """
    min_words, max_words = word_target(seed["level"])
    prompt = (
        PROMPT.replace("{sender_name}", seed["sender"]["name"])
        .replace("{sender_relation}", seed["sender"]["relation"])
        .replace("{situation}", seed["situation"].strip())
        .replace("{register}", seed["register"])
        .replace("{level}", seed["level"].upper())
        .replace("{min_words}", str(min_words))
        .replace("{max_words}", str(max_words))
        .replace("{points}", "\n".join(_point_rule(p) for p in seed["points"]))
        # Formal letters never get the deck layer: Behörden German has no room
        # for a learner's everyday vocab, and every forced fit the test rounds
        # produced ('Haben die genannten Zeiten für Sie "gelten"?') was in a
        # formal letter or a strained informal one. Enforced here, not by
        # prompt compliance.
        .replace(
            "{vocab}",
            _render_vocab([] if seed["register"] == "formal" else vocab_words),
        )
        .replace("{reader}", _reader_line(seed["register"], reader_name))
        .replace("{greeting_name}", _greeting_name(reader_name))
    )
    # 20s, not the 12s default: this generates a whole letter, where every
    # other judge in the repo rules on one sentence. A too-tight deadline here
    # doesn't fail — it silently burns the Cerebras leg and pays for the
    # OpenRouter fallback on every single request.
    # JUDGE-001 (2026-08-15): this generates a whole letter, not a verdict —
    # temperature=None keeps provider-default sampling so the same seed
    # doesn't produce the identical letter every time.
    llm = structured_judge_llm(LetterDraft, deadline_s=20.0, temperature=None)
    with generation_span(
        "briefkasten-writer",
        model=WRITER_MODEL,
        input_text=prompt,
        user_id=user_id,
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    # The model occasionally writes the field label into the field ("Betreff:
    # Termin …") — seen 1-in-8 in the v4 test round. Strip it; the frontend
    # renders its own label.
    result.betreff = re.sub(r"^\s*Betreff:\s*", "", result.betreff)
    return result
