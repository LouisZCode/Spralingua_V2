"""Post-session evaluator agent.

One-call LLM judge: given a transcript and a natural-language pass criterion,
returns {passed: bool, reason: str}. Same Cerebras `gpt-oss-120b` model used
by the conversation agent, but with `.with_structured_output(...)` and no
streaming — this is a single judgement call fired on disconnect.
"""

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config import openrouter_api_key, openrouter_base_url

EVALUATOR_MODEL = "openai/gpt-oss-120b"


class EvaluationResult(BaseModel):
    passed: bool = Field(description="Whether the learner satisfied the pass criterion")
    reason: str = Field(description="Specific evidence-cited explanation of pass/fail")


PROMPT = """You are a {language} language evaluator. You judge whether a learner
demonstrated a specific skill during a conversation.

Pass criterion:
{pass_criterion}

Conversation transcript:
{transcript}

Decide whether the learner satisfied the pass criterion. Cite specific evidence
from the transcript in your reasoning.
"""


async def evaluate(*, transcript: str, pass_criterion: str, language: str) -> EvaluationResult:
    llm = ChatOpenAI(
        model=EVALUATOR_MODEL,
        base_url=openrouter_base_url,
        api_key=openrouter_api_key,
        extra_body={"provider": {"order": ["cerebras"], "allow_fallbacks": True}},
    ).with_structured_output(EvaluationResult)
    return await llm.ainvoke(
        PROMPT.format(language=language, pass_criterion=pass_criterion, transcript=transcript)
    )
