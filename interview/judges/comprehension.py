"""Round-1 ("listen & retell") comprehension judge for the interview
exercise's two-round learner activity. Ported from
``interview_local/judges/comprehension.py`` (INTV-003 slice 2) — PROMPT is
user-curated and carried over verbatim.

Round 1: the learner hears one interviewer chunk WITHOUT ever seeing the
transcript, and retells in German what was said -- and, when the chunk
leads to a real question, what is being asked. This judge grades CONTENT
ONLY -- did the retell convey the core of what was said. It NEVER grades
grammar, word choice, word order or spelling; that ground belongs entirely
to a sibling pair of judges (interview/judges/grammar.py,
interview/judges/idiom.py) grading a DIFFERENT exercise (Round 2's spoken
answer). Broken German with the right idea PASSES; fluent German with the
wrong idea FAILS.

Same Cerebras ``gpt-oss-120b`` structured-output wiring as every other
interview judge (``agents.openrouter_llm.structured_judge_llm``), wired
WITHOUT observability -- same as the workbench: no ``generation_span``, no
Langfuse/OTel, no ledger writes at this layer (the ledger harvest lives in
``interview/routes.py``, over round 2's answer only).
"""

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, ConfigDict, Field

from ._schema_lint import lint_cerebras_schema

JUDGE_MODEL = "openai/gpt-oss-120b"


class ComprehensionVerdict(BaseModel):
    """Content-only verdict on a Round-1 listening retell. Never touches
    grammar, word choice, word order or spelling -- a passed retell can be
    grammatically broken German."""

    model_config = ConfigDict(extra="forbid")

    passed: bool = Field(
        description=(
            "True iff the retell conveys the core of what was said -- and, "
            "when the chunk leads to a real question, what is being asked -- "
            "regardless of German grammar, word choice or fluency"
        )
    )
    got_right: str = Field(
        description="1-2 short English sentences: what the learner correctly conveyed, content-wise"
    )
    nudge: str = Field(
        default="",
        description=(
            "Only when passed=false: one short English nudge pointing at "
            "what to listen for again (e.g. 'listen again to what she says "
            "about the team'). NEVER reveal the actual ground-truth summary "
            "or question -- point at the AREA to re-listen to, not the "
            "answer. Empty string when passed=true"
        ),
    )


lint_cerebras_schema(ComprehensionVerdict)


PROMPT = """# Role
You grade a Round-1 LISTENING COMPREHENSION check in a German job-interview practice exercise. The learner heard one chunk of the interviewer's audio -- WITHOUT ever seeing a transcript -- and retold, out loud in German, what they understood. You get the raw speech-recognition transcript of their retell.

# What you grade -- CONTENT ONLY
Did the retell convey the core of what the interviewer said? If the chunk leads to a real question directed at the candidate, did the retell also show they caught what is being asked? That is the whole list. A monologue's core is its main topic(s) at topic level -- see STEP 1c, enumerable detail is never required.

# What you must NEVER grade -- read this twice
German grammar, word order, verb forms, case endings, word choice, spelling, fluency. A retell can be grammatically shredded and still PASS if the content is right; a retell can be flawless, native-sounding German and still FAIL if it misses or invents the content. Grading grammar here is a failure of this task -- that ground belongs entirely to a sibling pair of judges elsewhere, over a different exercise (Round 2's spoken answer), never this one.

# The ground truth (never shown to the learner -- do not leak it into your output)
Chunk shape: {shape}
What was actually said (English ground truth): {summary}
The question this chunk leads to, if any (German): {question}

# The learner's spoken retell (raw speech-recognition transcript)
"{transcript}"

# STEP 1a -- separate transcription noise from a real content gap
This is speech recognition of SPOKEN GERMAN, not something the learner typed. Deepgram spells what it HEARS -- a trimmed verb ending, a "das" where "dass" was meant, a dropped filler word, or an unfinished trailing clause are the recognizer's artifacts, not gaps in what the learner understood. Never count any of these against `passed`; read past them to whether the underlying idea is there.

# STEP 1b -- credit phonetically garbled loanwords
English tech terms spoken inside a German sentence often come out as a similar-sounding German word or name instead: "Frontend" as "Freundin", "Backend" as "Wagner" or "Spakten", "coden" as "Kurden". When a garbled word is phonetically plausible for a key term the ground truth expects, credit it AS that term -- do not fail the retell for the recognizer's misfire (worked example 5).

# STEP 1c -- monologues: gist, not inventory
For a monologue (or the context half of context_plus_question), the retell only has to name the main topic(s) at topic level. Missing enumerable detail -- headcounts, department names, org-chart specifics -- must never by itself cause a fail (worked example 6). This has a limit: naming the wrong core topic, or inventing one that was not there, still fails (worked example 7).

# STEP 2 -- grade the content
## Worked example 1 -- awful German, right content -> PASS (control)
Ground truth: "She explains the team is remote-first and asks whether the candidate has experience working async across time zones." Retell: "Sie sagt Team ist remote und sie fragt ob ich Erfahrung habe mit arbeiten in anderen Zeitzonen zusammen async." Broken German, wrong articles, no real sentence structure -- but the content is entirely right. -> passed: true. got_right: "You caught that the team is remote-first and that she's asking about your experience working async across time zones." nudge: "".

## Worked example 2 -- fluent German, wrong content -> FAIL (control)
Ground truth: "He explains the company is a small, four-person consulting firm working only with industrial clients, and asks how the candidate combines technical skill with client communication." Retell: "Er hat erklärt, dass die Firma ziemlich groß ist und international tätig, und er hat gefragt, warum ich mich für diese Stelle interessiere." Grammatically perfect, natural German -- but the company size and the question are both invented. -> passed: false. got_right: "You caught that this was about the company and a question aimed at you." nudge: "Listen again to how big the company actually is, and to the exact question she asks about your skill set." (Never name the real company size or the real question -- that would leak the ground truth.)

## Worked example 3 -- catches the context, misses that a question was asked
Ground truth: shape context_plus_question. "He explains the team ships weekly and asks whether the candidate is comfortable with that pace." Retell: "Er sagt, dass das Team jede Woche etwas rausbringt." Context is right, but nothing shows the learner caught that a question followed. -> passed: false. got_right: "You got the weekly shipping cadence." nudge: "There's also a question at the end of this one -- listen again for what she's asking you directly."

## Worked example 4 -- monologue, correctly retold, nothing to ask about
Ground truth: shape monologue, no question. "She explains the interview will have three rounds: a call, a technical exercise, and an on-site." Retell: "Sie hat gesagt es gibt drei Runden, ein Call, eine technische Aufgabe und dann vor Ort." Right content, no question to catch. -> passed: true. got_right: "You got all three interview stages right." nudge: "".

## Worked example 5 -- phonetic loanword credit -> PASS (control, STEP 1b)
Ground truth: shape context_plus_question. "He explains he no longer writes code himself, calling it 'agent coding' now, and asks whether the candidate sees themselves more as a frontend or backend developer." Retell: "Er hat gesagt, dass er nicht mehr codet, er benutzt Agent Coding. Er hat gefragt, ob ich Wagner oder Freundin bin." "Wagner"/"Freundin" are Deepgram's phonetic misfires on "Backend"/"Frontend" -- this IS the frontend-or-backend question, correctly retold. -> passed: true. got_right: "You caught that he no longer codes himself and now works through agent coding, and that he asked whether you lean frontend or backend." nudge: "".

## Worked example 6 -- monologue gist, no enumerable detail -> PASS (STEP 1c)
Ground truth: shape monologue. "She introduces herself, explains she's covering for a colleague, and describes the company: a cooperative with a large headquarters staff and a digital-commerce subsidiary." Retell: "Julia hat gesagt, dass sie nicht so gut Englisch spricht, und dass sie für einen Kollegen einspringt. Der zweite Teil war, wie die Firma organisiert ist." No headcount, no department names, no subsidiary name -- but both topics (standing in for a colleague; company structure) are there at gist level. -> passed: true. got_right: "You caught that Julia is filling in for a colleague and that the second part covered how the company is organized." nudge: "".

## Worked example 7 -- wrong core topic still fails (control, STEP 1c limit)
Ground truth: same as example 6. Retell: "Sie hat gefragt, wie viel Gehalt ich mir vorstelle." This names a topic (salary) that never appeared in the monologue -- gist-level leniency does not cover inventing a topic. -> passed: false. got_right: "". nudge: "Listen again to what she actually introduces herself with, and to the two things she describes about the company."

# STEP 3 -- write the output
- `passed` -- true iff the core content (and the question, when the shape has one) came through, regardless of German quality, applying STEP 1a-1c above.
- `got_right` -- 1-2 short English sentences naming what they actually got right, content-wise. Empty string is fine when nothing was right.
- `nudge` -- ONLY when passed=false: one short English sentence that LOCATES where to re-listen (an area, a moment, "the part right after..."), and NEVER states the ground-truth summary or question. Bad, leaks it: "Listen again for the question about whether you see yourself more as a frontend or a backend developer." Good, locates without revealing: "Listen again to the part right after he talks about not coding anymore -- there's a question aimed at you there." Empty string when passed=true.
"""


async def judge_comprehension(transcript: str, shape: str, summary: str, question: str) -> ComprehensionVerdict:
    """One structured-output judgement call: Round-1 content comprehension
    only. No observability -- interview judges never wrap their calls in a
    generation span."""
    llm = structured_judge_llm(ComprehensionVerdict, temperature=0)
    prompt = (
        PROMPT
        .replace("{shape}", shape or "")
        .replace("{summary}", summary or "")
        .replace("{question}", question or "(none)")
        .replace("{transcript}", transcript)
    )
    result = await llm.ainvoke(prompt)
    if result.get("parsing_error") is not None:
        raise result["parsing_error"]
    return result["parsed"]
