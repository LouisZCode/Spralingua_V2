"""Grammar judge for the interview exercise's Round 2 (answer & improve).
Ported from ``interview_local/judges/grammar.py`` (INTV-003 slice 2) —
PROMPT is user-curated and carried over verbatim.

The learner listens to an interviewer chunk, records a spoken answer, and
this judge grades ONLY grammar -- word order, verb forms, case endings,
articles, prepositions. Word choice, idiomaticity and "the more German way
to say it" belong entirely to the sibling judge, ``interview/judges/idiom.py``
-- this module never touches that ground.

Same Cerebras ``gpt-oss-120b`` structured-output wiring as the main app's
other judges (``agents/openrouter_llm.py::structured_judge_llm``), wired
WITHOUT observability -- same as the workbench: no ``generation_span``, no
Langfuse/OTel, no ledger writes at this layer (the ledger harvest lives in
``interview/routes.py``, over this same answer, via the shared
``agents/error_extractor.py`` harvester, not this judge).
"""

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, Field

JUDGE_MODEL = "openai/gpt-oss-120b"


class GrammarSlip(BaseModel):
    """One real grammar mistake -- never a transcription artifact."""

    quote: str = Field(
        description="The learner's own words where it broke, VERBATIM from the transcript"
    )
    corrected: str = Field(description="The minimal repair of that quote, in German")
    note: str = Field(
        description="One short English line (AT MOST 10 words) naming the broken rule"
    )


class GrammarVerdict(BaseModel):
    """Grammar-only verdict on one spoken interview answer. Never touches
    word choice, idiomaticity or nativeness -- the sibling judge owns that."""

    ok: bool = Field(description="True iff there are no real grammar slips")
    slips: list[GrammarSlip] = Field(
        default_factory=list,
        description="Every real grammar mistake found; empty when the grammar is clean",
    )
    coach_note: str = Field(
        description="1-2 warm English sentences -- affirming when clean, encouraging when not"
    )


PROMPT = """# Role
You grade the GRAMMAR of one spoken answer in a German job-interview practice exercise. The learner heard the interviewer's question (or remark) below and answered spontaneously, out loud. You get the raw speech-recognition transcript of their answer.

# What you grade -- GRAMMAR ONLY
Word order, verb forms, case endings, articles, prepositions. That is the whole list.

# What you must NEVER grade -- read this twice
Word choice, collocations, idiomaticity, naturalness, or whether a native speaker would phrase it differently. A sentence can be perfectly grammatical and still sound translated or stiff -- that is NOT your job. A sibling judge grades nativeness entirely separately and owns that ground completely. If you flag a word choice or a stylistic preference here, you have failed this task.

# The interviewer's question/remark
"{question}"

# The learner's spoken answer (raw speech-recognition transcript)
"{transcript}"

# STEP 1 -- separate transcription noise from a real grammar break
This is speech recognition, not something the learner typed or saw on a screen. Deepgram spells what it HEARS, and its spelling choices are not the learner's mistakes.
- "ich glaube das er das gut macht" -> the connector is fine. "das" is Deepgram's homophone spelling of "dass" -- they sound identical. Never a slip.
- "ich geh dann meistens ins büro" -> "geh" for "gehe" is a trimmed ending the recognizer dropped, not the learner skipping it. Never a slip.
- A missing filler ("ähm"), missing punctuation, or a missing sentence break is never a slip -- infer sentence boundaries yourself and judge what was most plausibly said.
- The learner is answering spontaneously, speaking rather than writing -- forgive an incomplete trailing final clause (spoken answers trail off); an unfinished last sentence is not a slip.

## Control -- a REAL error still fails, even surrounded by artifacts like the ones above
"ich bleibe zuhause weil ich bin müde" -> **slip**. The weil-clause needs the verb LAST ("weil ich müde bin"); here it sits in position 2. Nothing about transcription explains that -- it is the actual word order the learner produced, so it is graded: quote "weil ich bin müde", corrected "weil ich müde bin", note "weil sends the verb to the end".

# STEP 2 -- grade
- `ok` -- true iff, after discounting transcription noise, there are no real grammar slips left.
- `slips` -- one entry per real grammar mistake: `quote` (the learner's own words, VERBATIM from the transcript -- never paraphrase or trim), `corrected` (the minimal repair, in German, keeping their words and idea), `note` (ONE short English line, AT MOST 10 words, naming the broken rule -- the correction sits right next to it, so name the WHY, not the fix). Do not repair or comment on anything outside grammar -- a wrong-but-grammatical word choice gets no entry here.
- `coach_note` -- 1-2 warm English sentences. When `ok=true`, affirm the grammar was clean. When `ok=false`, be encouraging, not clinical -- this is spontaneous spoken German, slips are normal.
"""


async def judge_grammar(question: str, transcript: str) -> GrammarVerdict:
    """One structured-output judgement call: grammar only. No observability
    -- interview judges never wrap their calls in a generation span."""
    llm = structured_judge_llm(GrammarVerdict, temperature=0)
    prompt = PROMPT.replace("{question}", question).replace("{transcript}", transcript)
    result = await llm.ainvoke(prompt)
    if result.get("parsing_error") is not None:
        raise result["parsing_error"]
    return result["parsed"]
