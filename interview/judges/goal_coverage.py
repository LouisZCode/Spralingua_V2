"""Round-2 ("read & answer") goal-coverage judge for the interview
exercise's two-round learner activity. Ported from
``interview_local/judges/goal_coverage.py`` (INTV-003 slice 2) — PROMPT is
user-curated and carried over verbatim.

Round 2 hands the learner the brief's question and 1-3 goals up front
("Your answer should: ..."), they record a spoken answer, and THIS judge
checks -- per goal -- whether the CONTENT of that answer addresses it.
Grammar is entirely out of scope here; a sibling pair of judges
(interview/judges/grammar.py, interview/judges/idiom.py) already grades
this exact same answer for grammar and nativeness, and this judge must
never repeat a grammar finding under a different name. An answer can be
full of grammar mistakes and still cover every goal.

Same Cerebras ``gpt-oss-120b`` structured-output wiring as every other
interview judge (``agents.openrouter_llm.structured_judge_llm``), wired
WITHOUT observability (same as the workbench).
"""

from agents.openrouter_llm import structured_judge_llm
from pydantic import BaseModel, ConfigDict, Field

from ._schema_lint import lint_cerebras_schema

JUDGE_MODEL = "openai/gpt-oss-120b"


class GoalCoverageItem(BaseModel):
    """One goal's coverage verdict -- content only."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(description="The goal text, copied VERBATIM from the input goals list, same order")
    covered: bool = Field(
        description="True iff the answer's CONTENT addresses this goal, regardless of grammar or fluency"
    )
    note: str = Field(
        description=(
            "One short English line. If covered: what they said that covers "
            "it. If not covered: what was missing -- content only, never a "
            "grammar observation"
        )
    )


class GoalCoverageVerdict(BaseModel):
    """Per-goal content-coverage verdict on one spoken Round-2 answer. Never
    touches grammar -- a sibling pair of judges owns that entirely."""

    model_config = ConfigDict(extra="forbid")

    items: list[GoalCoverageItem] = Field(
        description="One entry per input goal, same order as the input goals list, one-to-one"
    )


lint_cerebras_schema(GoalCoverageVerdict)


PROMPT = """# Role
You grade GOAL COVERAGE for one spoken answer in a German job-interview practice exercise. The learner was shown a question and 1-3 answer goals up front, then answered spontaneously, out loud. You get the raw speech-recognition transcript of their answer.

# What you grade -- CONTENT ONLY, per goal
For EACH goal below, did the answer's content actually address it? That is the whole list.

# What you must NEVER grade -- read this twice
Grammar, word order, verb forms, case endings, articles, spelling, word choice, fluency. A sibling pair of judges grades all of that separately over this exact same answer -- if you flag a grammar issue here, even inside a `note`, you have failed this task and duplicated their job under a different name. Judge only whether the IDEA the goal asks for is present.

## Control -- bad grammar, goal still covered
Goal: "Describe a time you adapted to a change at short notice." Answer (badly broken German): "Ich war arbeiten bei Firma und Kunde hat geändert Anforderung ganz kurzfristig und ich habe reagiert schnell und gemacht neue Plan." Despite the grammar being a wreck, the content is there: a client changed requirements on short notice, and the learner adapted. -> covered: true, note: "You gave a concrete example: a client changed requirements last-minute and you built a new plan quickly."

## Worked example -- goal partially addressed, judged as not covered
Goal: "Explain how you communicate with non-technical stakeholders." Answer: "Ich arbeite gern im Team und finde Kommunikation sehr wichtig." This affirms that communication matters to them, but never actually explains HOW they communicate with non-technical people specifically -- no method, no example. -> covered: false, note: "You said communication matters to you, but didn't say how you actually explain things to non-technical people."

## Worked example -- goal clearly covered
Goal: "Say whether you prefer working independently or with clear guidance, and explain why." Answer: "Ich arbeite lieber eigenständig, weil ich in meinem letzten Job viel Freiraum hatte und dabei am produktivsten war." States the preference (independently) AND gives the reason (most productive with freedom, from experience). -> covered: true, note: "You said you prefer working independently and gave a concrete reason -- past experience being most productive with freedom."

# The question this answer responds to (context only)
"{question}"

# The goals to check, in order
{goals}

# The learner's spoken answer (raw speech-recognition transcript)
"{transcript}"

# STEP 1 -- separate transcription noise from a real content gap
This is speech recognition of spontaneous spoken German, not something the learner typed. A trimmed verb ending, a "das" where "dass" was meant, a dropped filler, or an unfinished trailing clause are the recognizer's artifacts -- never count these against coverage; read past them to whether the idea is there.

# STEP 2 -- write the output
`items` -- exactly one entry per goal above, IN THE SAME ORDER: `goal` (copied verbatim), `covered` (content-only judgement), `note` (one short English line, content only, never a grammar remark).
"""


async def judge_goal_coverage(transcript: str, goals: list[str], question: str) -> GoalCoverageVerdict:
    """One structured-output judgement call: per-goal content coverage
    only. No observability -- interview judges never wrap their calls in a
    generation span."""
    llm = structured_judge_llm(GoalCoverageVerdict, temperature=0)
    goals_block = "\n".join(f"{i + 1}. {g}" for i, g in enumerate(goals))
    prompt = (
        PROMPT
        .replace("{question}", question or "")
        .replace("{goals}", goals_block)
        .replace("{transcript}", transcript)
    )
    result = await llm.ainvoke(prompt)
    if result.get("parsing_error") is not None:
        raise result["parsing_error"]
    return result["parsed"]
