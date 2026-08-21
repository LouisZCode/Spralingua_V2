"""Nativeness judge ("the more German way to say it") for the interview
exercise's Round 2 (answer & improve). Ported from
``interview_local/judges/idiom.py`` (INTV-003 slice 2) — PROMPT is
user-curated and carried over verbatim.

Sibling of ``interview/judges/grammar.py``: that module grades correctness,
this one grades NATIVENESS -- word choice, collocations, natural phrasing,
spoken-German flow. It never flags a grammar mistake; when the learner's
answer has a real grammar slip, this judge's rewritten ``german_version``
silently uses correct German, but no ``suggestions`` entry may be about
grammar.

Same Cerebras ``gpt-oss-120b`` structured-output wiring as
``interview/judges/grammar.py``, traced with a ``generation_span`` per call
since 2026-08-20.
"""

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

JUDGE_MODEL = "openai/gpt-oss-120b"


class IdiomSuggestion(BaseModel):
    """One place the learner's phrasing works but isn't how a native
    speaker would say it -- never a grammar correction."""

    original: str = Field(description="Their phrase, VERBATIM from the transcript")
    german_way: str = Field(description="How a native speaker would put the same idea")
    why: str = Field(
        description="Short English reason -- what makes the native version more natural"
    )


class IdiomVerdict(BaseModel):
    """Nativeness-only verdict on one spoken interview answer. Never
    touches grammar correctness -- the sibling judge owns that entirely."""

    german_version: str = Field(
        description=(
            "The WHOLE answer re-said the way a native German speaker would "
            "naturally say it in a job interview -- same meaning, same "
            "simplicity, spoken register. Silently uses correct grammar, but "
            "otherwise stays as close to the learner's own answer as a "
            "genuinely natural version allows"
        )
    )
    suggestions: list[IdiomSuggestion] = Field(
        default_factory=list,
        description=(
            "Every place word choice or phrasing was unidiomatic; empty when "
            "the answer already sounds native"
        ),
    )
    coach_note: str = Field(
        description=(
            "1-2 warm English sentences -- affirming when it already sounds "
            "German, encouraging when not"
        )
    )


PROMPT = """# Role
You judge how NATIVE-SOUNDING one spoken answer is, in a German job-interview practice exercise. The learner heard the interviewer's question (or remark) below and answered spontaneously, out loud. You get the raw speech-recognition transcript of their answer.

# What you grade -- NATIVENESS ONLY
Word choice, collocations, natural phrasing, spoken-German flow -- including natural spoken particles (halt/eben/tatsächlich/ja/schon) where a native speaker would drop one in. That is the whole list.

# What you must NEVER grade -- read this twice
Grammar. Word order, verb forms, case endings, articles, prepositions all belong entirely to a sibling judge. If the learner's answer has a real grammar mistake, you may quietly write correct grammar into `german_version`, but not ONE entry in `suggestions` may be about a grammar rule. A suggestion is only ever about word choice or phrasing.

## Control -- a real grammar mistake sits right next to a real word-choice one; only flag the word choice
"ich bleibe in leipzig weil ich habe dort eine wohnung gefunden" has TWO things wrong with it: "bleibe" (stay) is the wrong verb for describing a move -- a native says "gezogen" (moved) -- AND the weil-clause verb sits in the wrong position ("weil ich habe ... gefunden" instead of "weil ich ... gefunden habe"). You flag ONLY the first one. `suggestions` gets exactly one entry: original "ich bleibe in leipzig", german_way "ich bin nach Leipzig gezogen", why "a native says 'gezogen' (moved) here, not 'bleibe' (stay) -- that's the natural verb for describing a move". You do NOT add a second suggestion about the weil-clause verb position, and `why` never says anything like "the verb belongs at the end" or "clause order" -- that sentence is the sibling judge's job, not yours. `german_version` still silently fixes the clause order ("...weil ich dort eine Wohnung gefunden habe") since the rewrite must be correct German, but nothing in `suggestions` may name why that fix was made.

# The interviewer's question/remark
"{question}"

# The learner's spoken answer (raw speech-recognition transcript)
"{transcript}"

# STEP 1 -- separate transcription noise from a real phrasing choice
This is speech recognition, not something the learner typed. Never "improve" a Deepgram artifact.
- "ich glaube das er das gut macht" -> "das" is Deepgram's spelling of "dass" -- not a word choice the learner made. Leave it alone.
- "ich geh dann meistens ins büro" -> "geh" for "gehe" is a trimmed ending from the recognizer. Leave it alone.
- A missing filler, missing punctuation, or a missing sentence break is never something to rewrite around -- judge the sentence the learner most plausibly said.

# CRITICAL -- do not invent rewrites to seem useful
Most spontaneous answers from a learner with real German are ALREADY close to how a native would say it. When that is true, `german_version` must come back essentially UNCHANGED from the transcript (same words, same order, same simplicity -- fixing only real grammar noise per STEP 1), `suggestions` must be empty, and `coach_note` should say plainly that it already sounds German. Worked example: "Ich habe drei Jahre als Datenanalyst gearbeitet und dabei viel mit Python gemacht." -> this is exactly how a German would say it. `german_version` stays the same sentence, `suggestions` is [], `coach_note` affirms it already sounds natural. Do NOT swap in a fancier synonym, reorder clauses for no reason, or add a particle that wasn't earned -- every unnecessary change is a mistake here.

# When it genuinely sounds translated
Some answers carry the shape of another language -- an English idiom translated word-for-word, an overly formal/written register in a spoken answer, a missing spoken particle a native would reach for. THAT is when you rewrite and suggest. Worked example: "Ich bin sehr passioniert über künstliche Intelligenz und ich bin excited für diese Möglichkeit." -> "passioniert über" and "excited für" are English shapes wearing German words; a native says "Ich interessiere mich sehr für künstliche Intelligenz und freue mich total auf diese Möglichkeit." `suggestions` gets two entries, one per swap, each `why` naming the English-shaped collocation being replaced -- never a grammar reason.

# STEP 2 -- write the output
- `german_version` -- the WHOLE answer, re-said naturally, same meaning and same simplicity (short main clauses -- do not upgrade a learner's B1 German into C1; do not add ideas they didn't say), spoken interview register (not stiff, not written-formal). Grammar is silently correct in it, but this is not a grammar rewrite -- only touch what a native would actually phrase differently.
- `suggestions` -- one entry per real unidiomatic spot: `original` (VERBATIM from the transcript), `german_way` (the natural replacement), `why` (short English reason -- word choice or phrasing only, never a grammar rule). Empty list when the answer already sounds native.
- `coach_note` -- 1-2 warm English sentences. When the answer already sounded native, say so plainly and warmly. Otherwise, be encouraging about the direction to grow in.
"""


async def judge_idiom(question: str, transcript: str) -> IdiomVerdict:
    """One structured-output judgement call: nativeness only. No
    correctness, traced as one ``interview-idiom-judge`` generation under
    the route's root span."""
    llm = structured_judge_llm(IdiomVerdict, temperature=0)
    prompt = PROMPT.replace("{question}", question).replace("{transcript}", transcript)
    with generation_span("interview-idiom-judge", model=JUDGE_MODEL, input_text=prompt) as span:
        parsed, usage, response_metadata = unwrap_structured_output(await llm.ainvoke(prompt))
        record_generation_output(span, parsed.model_dump_json(), usage, response_metadata)
    return parsed
