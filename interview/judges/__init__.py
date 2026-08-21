"""Judges for the interview exercise (INTV-003 slice 2), ported from
``interview_local/judges/``.

Four plain LLM judges: ``comprehension.py`` grades round-1 ("listen &
retell") content only; ``grammar.py`` and ``idiom.py`` grade round-2's
spoken answer for correctness and nativeness respectively; ``goal_coverage.py``
grades round-2 per-goal content coverage when the chunk's brief has goals.
All four go through ``agents.openrouter_llm.structured_judge_llm``
(Cerebras-direct + OpenRouter fallback). Since the Langfuse-v4 pass
(2026-08-20) every judge call is wrapped in ``generation_span`` — one
``interview-*-judge`` observation per call, nesting under the route's root
span with user/session via baggage.
Ledger writes are NOT made by any judge here — that happens exactly once,
in ``interview/routes.py``'s background harvest, via the shared
``agents/error_extractor.py::extract_errors`` + ``database.repository.
record_grammar_error`` path every other drill/tandem session uses.
"""
