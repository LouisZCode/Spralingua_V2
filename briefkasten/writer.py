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
- `informal` — you and the reader are friends. Use "du" throughout. Everyday German, contractions, the warmth of a real friend writing. Greeting like "Liebe/Lieber {reader}" or "Hallo {reader}", closing like "Viele Grüße", "Liebe Grüße", "Bis bald".
- `formal` — this is a business or official letter. Use "Sie" throughout. Real German Behörden/Geschäft register: complete sentences, no chattiness, no exclamation marks. Greeting "Sehr geehrte Frau …" / "Sehr geehrter Herr …", closing "Mit freundlichen Grüßen".
Never mix the two. A single "du" in a formal letter, or "Sehr geehrte" in a friendly one, ruins the exercise — the learner is being graded on matching your register.

# Level and length
The reader is at level {level}. Write at that level — vocabulary and sentence structures they can actually read. Aim for about {min_words}-{max_words} words.

# Who you are writing to
{reader}

# What the letter must do
- Cover the situation above naturally — as a person writing, not as a summary of it.
- Embed 2-3 real questions. The learner needs something to answer.
- Leave room for the reply: the letter must make each of these feel natural to respond to, WITHOUT ever listing them:
{points}

# The learner's own vocabulary
{vocab}

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

_VOCAB_SOME = """These are German words the reader is currently learning. Work a few of them into the letter WHERE THEY GENUINELY FIT the situation — naturally, inside real sentences. Never force one in, never use all of them, never explain or translate one, never draw attention to them. If a word does not fit this letter, leave it out; that is the normal case. The English gloss only tells you which sense is being learned:
{words}"""


def _reader_line(register: str, reader_name: str | None) -> str:
    """How the sender should greet the learner, given what we know of them."""
    if register == "formal":
        return _READER_ANON_FORMAL
    if reader_name:
        return _READER_NAMED.replace("{name}", reader_name)
    return _READER_ANON_INFORMAL


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
        .replace("{points}", "\n".join(f"  - {p}" for p in seed["points"]))
        .replace("{vocab}", _render_vocab(vocab_words))
        .replace("{reader}", _reader_line(seed["register"], reader_name))
    )
    # 20s, not the 12s default: this generates a whole letter, where every
    # other judge in the repo rules on one sentence. A too-tight deadline here
    # doesn't fail — it silently burns the Cerebras leg and pays for the
    # OpenRouter fallback on every single request.
    llm = structured_judge_llm(LetterDraft, deadline_s=20.0)
    with generation_span(
        "briefkasten-writer",
        model=WRITER_MODEL,
        input_text=prompt,
        user_id=user_id,
    ) as span:
        result, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, result.model_dump_json(), usage, response_metadata)
    return result
