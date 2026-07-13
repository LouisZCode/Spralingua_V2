"""Post-session evaluator agent.

Per-goal LLM judge with score + threshold. Given a transcript, a numbered
list of communicative goals, a pass threshold (0-100), and the target
language, returns a structured verdict per goal plus an overall pass/fail
relative to the threshold.

Same Cerebras `gpt-oss-120b` model used by the conversation agent, but with
`.with_structured_output(EvaluationResult)` (nested Pydantic) and no
streaming — this is a single judgement call fired on disconnect.

Template substitution uses ``str.replace`` rather than ``str.format``
because the prompt body contains literal JSON examples whose braces would
collide with Python's format-string parsing.
"""

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agents.observability import (
    generation_span,
    record_generation_output,
    unwrap_structured_output,
)
from config import openrouter_api_key, openrouter_base_url

EVALUATOR_MODEL = "openai/gpt-oss-120b"


class GoalResult(BaseModel):
    goal: str = Field(description="The goal text copied verbatim from input")
    passed: bool = Field(description="Whether the learner performed this goal in the target language")
    evidence: str = Field(description="Exact quote from the student's turn that performs or attempts this goal, or 'none' if no relevant student turn exists")
    reasoning: str = Field(description="One or two short sentences in clear English describing the pass/fail decision")


class EvaluationResult(BaseModel):
    score: int = Field(ge=0, le=100, description="Percent of goals passed, rounded")
    pass_threshold: int = Field(ge=0, le=100, description="Copy of the input pass_threshold")
    passed: bool = Field(description="True iff score >= pass_threshold")
    goals: list[GoalResult] = Field(description="One entry per input goal, in the same order")


PROMPT = """# Role
You evaluate whether a language learner successfully performed the required communicative goal(s) in {language} during a conversation. The student has already studied this material; this is a competence check, not formative feedback. Either they can do it, or they need to study more.

# Evaluation principle
Be strict. For each goal:
- PASS only if the student performed the goal correctly IN {language} during the conversation. Grammar, gender, case, or spelling mistakes inside a {language} attempt do not cause a fail — the communicative act was still performed.
- FAIL if the student performed the act in another language, performed the wrong act, or did not perform it at all.

A goal can be passed at any point in the transcript. One clear instance is enough; multiple attempts are fine.

# Scoring
- Each goal carries an equal share of 100 points.
- score = round((number of passed goals / total number of goals) * 100)
- For a single goal, score is either 0 or 100.
- The lesson passes if score >= {pass_threshold}.

# Inputs
- language: the target language the student must use (e.g., German, Spanish, French).
- goals: one or more lesson goals, provided as a numbered list. Each goal is a sentence describing one communicative act the student must perform.
- pass_threshold: integer between 0 and 100. The minimum score required for the lesson to pass.
- transcript: the chat history between the student and the conversation partner, in chat format with role labels. "User:" lines are the student; "Bot:" lines are the conversation partner.

# Before answering
For each goal in order, find any student turn(s) that attempt or perform it. Decide pass/fail per goal based on the principle above. Then compute the score and compare it to {pass_threshold}.

# Output
Respond with ONLY valid JSON, no surrounding text, no code fences, no comments:
{
  "score": <integer 0-100>,
  "pass_threshold": <copy of the input pass_threshold>,
  "passed": <true | false>,
  "goals": [
    {
      "goal": "<copy the goal text exactly as provided>",
      "passed": <true | false>,
      "evidence": "<exact quote from the student's turn that performs or attempts this goal, or 'none' if no relevant student turn exists>",
      "reasoning": "<one or two short sentences in clear English. For a pass, briefly note what the student did. For a fail, state specifically what was wrong (wrong language, wrong act, or no attempt) and point to what to review in {language}.>"
    }
  ]
}

The "goals" array must contain one entry per input goal, in the same order. "passed" at the top level must equal (score >= pass_threshold).

# Examples
(Examples below use German as the target language for illustration. Apply the same logic to whatever {language} is.)

Example 1 — single goal, pass
language: German
goals:
1. Greet the stranger back.
pass_threshold: 100
transcript:
Bot: Hallo!
User: Hallo, guten Tag!

{
  "score": 100,
  "pass_threshold": 100,
  "passed": true,
  "goals": [
    {
      "goal": "Greet the stranger back.",
      "passed": true,
      "evidence": "Hallo, guten Tag!",
      "reasoning": "The student returned the greeting in German."
    }
  ]
}

Example 2 — single goal, fail (wrong language)
language: German
goals:
1. Greet the stranger back.
pass_threshold: 100
transcript:
Bot: Hallo!
User: Hi there!

{
  "score": 0,
  "pass_threshold": 100,
  "passed": false,
  "goals": [
    {
      "goal": "Greet the stranger back.",
      "passed": false,
      "evidence": "Hi there!",
      "reasoning": "The student greeted in English instead of German. Review German greetings such as Hallo, Guten Tag, or Guten Morgen before retrying."
    }
  ]
}

Example 3 — multiple goals, all pass
language: German
goals:
1. Greet the stranger back.
2. Tell them your name.
3. Ask their name in return.
pass_threshold: 80
transcript:
Bot: Hallo!
User: Hallo! Ich heiße Luis. Wie heißt du?
Bot: Ich bin Anna.

{
  "score": 100,
  "pass_threshold": 80,
  "passed": true,
  "goals": [
    {
      "goal": "Greet the stranger back.",
      "passed": true,
      "evidence": "Hallo!",
      "reasoning": "The student returned the greeting in German."
    },
    {
      "goal": "Tell them your name.",
      "passed": true,
      "evidence": "Ich heiße Luis.",
      "reasoning": "The student stated their name in German."
    },
    {
      "goal": "Ask their name in return.",
      "passed": true,
      "evidence": "Wie heißt du?",
      "reasoning": "The student asked for the partner's name in German."
    }
  ]
}

Example 4 — multi-goal, score meets threshold exactly (4/5 = 80, threshold 80 → pass)
language: German
goals:
1. Greet the stranger.
2. Tell them your name.
3. Ask their name.
4. Say where you are from.
5. Say goodbye politely.
pass_threshold: 80
transcript:
Bot: Hallo!
User: Hallo! Ich heiße Luis. Wie heißt du?
Bot: Ich heiße Anna. Woher kommst du?
User: Ich komme aus Mexiko. Bye!

{
  "score": 80,
  "pass_threshold": 80,
  "passed": true,
  "goals": [
    {
      "goal": "Greet the stranger.",
      "passed": true,
      "evidence": "Hallo!",
      "reasoning": "The student greeted in German."
    },
    {
      "goal": "Tell them your name.",
      "passed": true,
      "evidence": "Ich heiße Luis.",
      "reasoning": "The student stated their name in German."
    },
    {
      "goal": "Ask their name.",
      "passed": true,
      "evidence": "Wie heißt du?",
      "reasoning": "The student asked for the partner's name in German."
    },
    {
      "goal": "Say where you are from.",
      "passed": true,
      "evidence": "Ich komme aus Mexiko.",
      "reasoning": "The student stated their origin in German."
    },
    {
      "goal": "Say goodbye politely.",
      "passed": false,
      "evidence": "Bye!",
      "reasoning": "The student said goodbye in English instead of German. Review German farewells such as Tschüss, Auf Wiedersehen, or Bis bald."
    }
  ]
}

Example 5 — multi-goal, score below threshold (3/5 = 60, threshold 80 → fail)
language: German
goals:
1. Greet the stranger.
2. Tell them your name.
3. Ask their name.
4. Say where you are from.
5. Say goodbye politely.
pass_threshold: 80
transcript:
Bot: Hallo!
User: Hi! My name is Luis. What's your name?
Bot: Ich heiße Anna. Woher kommst du?
User: Ich komme aus Mexiko. Tschüss!

{
  "score": 60,
  "pass_threshold": 80,
  "passed": false,
  "goals": [
    {
      "goal": "Greet the stranger.",
      "passed": false,
      "evidence": "Hi!",
      "reasoning": "The student greeted in English instead of German. Review German greetings such as Hallo or Guten Tag."
    },
    {
      "goal": "Tell them your name.",
      "passed": false,
      "evidence": "My name is Luis.",
      "reasoning": "The student stated their name in English. Review German phrases such as Ich heiße… or Mein Name ist…"
    },
    {
      "goal": "Ask their name.",
      "passed": false,
      "evidence": "What's your name?",
      "reasoning": "The student asked in English. Review the German question Wie heißt du? or Wie ist dein Name?"
    },
    {
      "goal": "Say where you are from.",
      "passed": true,
      "evidence": "Ich komme aus Mexiko.",
      "reasoning": "The student stated their origin in German."
    },
    {
      "goal": "Say goodbye politely.",
      "passed": true,
      "evidence": "Tschüss!",
      "reasoning": "The student said goodbye in German."
    }
  ]
}

# Now evaluate
language: {language}
goals:
{goals}
pass_threshold: {pass_threshold}
transcript:
{transcript}
"""


async def evaluate(
    *,
    transcript: str,
    goals: list[str],
    pass_threshold: int,
    language: str = "English",
    session_id: str | None = None,
) -> EvaluationResult:
    goals_text = "\n".join(f"{i}. {g}" for i, g in enumerate(goals, 1))
    rendered = (
        PROMPT
        .replace("{language}", language)
        .replace("{goals}", goals_text)
        .replace("{pass_threshold}", str(pass_threshold))
        .replace("{transcript}", transcript)
    )
    llm = ChatOpenAI(
        model=EVALUATOR_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        timeout=30,
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": True}},
    ).with_structured_output(EvaluationResult, include_raw=True)
    # OBS-007: `session_id` is the conversation's Langfuse session, so the
    # post-session judgement files next to the turns it judged.
    with generation_span(
        "goal-eval",
        model=EVALUATOR_MODEL,
        input_text=rendered,
        session_id=session_id,
    ) as span:
        result, usage = unwrap_structured_output(await llm.ainvoke(rendered))
        record_generation_output(span, result.model_dump_json(), usage)
    return result
