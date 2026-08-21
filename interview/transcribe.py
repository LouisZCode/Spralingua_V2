"""Deepgram transcription for the interview exercise (INTV-003 slice 2),
ported from ``interview_local/app.py``'s Part-2 helpers
(``_transcribe_answer``, ``_transcribe_comprehension``, ``_keyword_boosts``
and the tech-loanword allowlist that feeds it).

Deliberately NOT ``satz/examiner.py::transcribe_attempt``, for the same
reason the workbench avoided it: that helper pins ``nova-3`` and wraps every
call in ``agents.observability.generation_span``, where this exercise pins
``nova-2`` (round 1's ``keywords`` boosting is a nova-2-only parameter —
nova-3's equivalent, ``keyterm``, is English-only). Since 2026-08-20 both
calls are traced: each opens an ``stt`` generation span (same shape as
``satz/examiner.py``'s), which nests under the route's root span because
``interview/routes.py`` opens that root BEFORE transcribing — so every
network call in this exercise is now under the Langfuse umbrella (the
judges got their spans the same day; the ledger harvest already had one
via ``agents/error_extractor.py``).
"""

import re
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

import httpx
from loguru import logger

from agents.observability import generation_span, record_generation_output
from config import deepgram_api_key

# Deepgram prerecorded endpoint for both round-1 (retell) and round-2
# (answer) transcription.
_DEEPGRAM_ANSWER_URL = (
    "https://api.deepgram.com/v1/listen"
    "?model=nova-2&language=de&punctuate=true&smart_format=true"
)

# Defensive cap on a Deepgram transcript before it's handed to a judge —
# normal spoken turns land nowhere near this; it exists only to reject a
# pathological transcript rather than spend a judge call on it.
TRANSCRIPT_MAX_LEN = 4000


async def transcribe_answer(audio: bytes, content_type: Optional[str]) -> str:
    """One POST to Deepgram's prerecorded REST API: finished clip in,
    transcript out. Browsers send webm/opus (Chrome/Firefox) or mp4/aac
    (Safari); Deepgram accepts either as-is via Content-Type, same as the
    main app's other upload paths."""
    headers = {
        "Authorization": f"Token {deepgram_api_key}",
        "Content-Type": content_type or "audio/webm",
    }
    with generation_span(
        "stt",
        system="deepgram",
        model="nova-2",
        operation="transcription",
        input_text=f"[{len(audio)} bytes, {content_type or 'audio/webm'}]",
    ) as span:
        span.set_attribute("audio.bytes", len(audio))
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(_DEEPGRAM_ANSWER_URL, headers=headers, content=audio)
            resp.raise_for_status()
            body = resp.json()
        transcript = body["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        record_generation_output(span, transcript)
    return transcript


# Round-1 ("listen & retell") Deepgram keyword boosting -- curated
# tech-loanword allowlist (INTV-001), replacing an earlier "boost every
# capitalized noun/proper-noun" heuristic. That broad approach degraded
# clean transcripts in an A/B test (2026-08-16): boosting the generic
# German word "Bereich" made Deepgram DROP the correct "Frontend" a few
# words later, and boosting the sentence-initial auxiliary "Wärst" caused
# an unrelated substitution elsewhere in the SAME transcript ("der way to
# go" -> "der Wärst go"). Narrow, hand-picked loanword lists were clean by
# comparison, hence this allowlist.
#
# Curated 2026-08-17 by reading every chunk APPROVED in each recording
# dir's review.json across all 9 dirs under data/chunks/ -- both the
# chunk's own "text" (chunks.json) and its brief's "question.text"
# (briefs.json) -- and hand-picking genuine English/tech loanwords out of
# the ~2800 unique tokens that turned up. Lowercased match keys.
# Deliberately EXCLUDED even when frequent: native German words (however
# capitalized -- "Modell" is native German, not the English "Model"),
# words shorter than 3 chars, one-off proprietary product names specific
# to a single employer (e.g. "OctoPlant", "Lucanet"), generic recruiting/
# business English (Assessment, Stakeholder, Review), and anything spelled
# identically in German and already well assimilated ("Team", "Test",
# "Server", "Feedback" -- these already transcribe fine; boosting them
# only adds corruption risk for no gain). "Cognigy" is kept despite being
# one company's product because it recurred across TWO independent
# interview dirs as attested industry vocabulary, not a one-off brand.
#
# "code" was deliberately DROPPED after the Deepgram A/B below (see
# transcribe_comprehension's keyword-boost step): boosting "Code:2"
# hallucinated a substitution elsewhere in the SAME transcript ("oder so
# was" -> "oder Code was"), reproducing a prior finding almost exactly.
# Crucially, EVERY test run (boosted or not) already transcribed the
# genuine "Code" in this fixture correctly without any boost at all --
# so dropping it costs nothing while removing a demonstrated corruption
# risk. "Coding" (longer, more distinctive) showed no such issue and
# stays. Boosting "Frontend" was also observed to reproducibly drop the
# adjacent "Bereich" in "Frontend-Bereich" (tested at both :2 and :1.5,
# alone and combined with "Backend") -- "Backend-Bereich" was unaffected
# either way. Judged lower-severity than Code's case (an adjacent
# low-content filler word goes missing, vs. a wrong word appearing
# elsewhere) and kept given "Frontend" is the single highest-value term
# in the whole corpus survey; documented here rather than silently
# dropped. Re-evaluate if a future A/B shows the same pattern degrading a
# real learner transcript.
#
# To extend: add a lowercase entry to _TECH_LOANWORDS, and ONLY if
# str.capitalize() would render it wrong (acronyms, camelCase product
# names) also add its exact display form to _TECH_LOANWORD_CASING.
_TECH_LOANWORDS = frozenset({
    # roles / disciplines
    "frontend", "backend", "fullstack", "engineer", "engineering",
    "engineers", "developer", "development", "scientist", "scientists",
    "scientisten",
    # AI / ML / LLM vocabulary
    "agent", "agenten", "agentic", "agenting", "agents", "prompt",
    "prompts", "api", "llm", "llms", "rag", "mcp", "embeddings",
    "overfitting", "reranking", "token", "tokens", "gpt", "chatgpt",
    "judge",
    # coding / languages / frameworks ("code" deliberately excluded, see
    # comment above)
    "coding", "vibe", "python", "java", "javascript", "typescript",
    "ruby", "react", "angular", "node",
    # infra / cloud / devops
    "cloud", "azure", "bedrock", "deploy", "deployed", "backup",
    "pipeline", "pipelines", "observability", "latency", "git", "github",
    "gitlab", "devops", "cicd", "stack", "http",
    # data
    "data", "dataset",
    # AI vendors / products
    "claude", "anthropic", "openai", "ollama", "copilot", "langchain",
    "langfuse", "langgraph", "cognigy", "sap",
    # agile / delivery
    "bugs", "backlog", "sprint", "acceptance", "mvp", "mvps", "workflow",
    "workflows",
    # conversational AI / voice
    "bot", "bots", "chatbot", "voice", "containment", "faq", "stt", "tts",
    # misc tech-consulting acronyms
    "fde", "crm", "rpa",
})

# Display casing for the (few) allowlist entries where Python's
# str.capitalize() would get it wrong -- acronyms that should stay all
# upper, and camelCase product names. Everything else in
# _TECH_LOANWORDS falls back to .capitalize() in keyword_boosts, which
# already matches its corpus-typical form (a leading capital, e.g.
# "Frontend", "Cognigy").
_TECH_LOANWORD_CASING = {
    "api": "API", "llm": "LLM", "llms": "LLMs", "rag": "RAG", "mcp": "MCP",
    "gpt": "GPT", "chatgpt": "ChatGPT", "sap": "SAP", "crm": "CRM",
    "rpa": "RPA", "fde": "FDE", "faq": "FAQ", "stt": "STT", "tts": "TTS",
    "http": "HTTP", "cicd": "CICD", "devops": "DevOps", "github": "GitHub",
    "gitlab": "GitLab", "javascript": "JavaScript",
    "typescript": "TypeScript", "openai": "OpenAI", "mvp": "MVP",
    "mvps": "MVPs",
}

# Letters plus inline hyphens (ASCII "-" and the non-breaking hyphen
# curated briefs sometimes carry, U+2010/U+2011) -- picks whole, possibly
# hyphenated, tokens out of a chunk/question string for allowlist matching.
_KEYWORD_TOKEN_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+(?:[-‐‑][A-Za-zÄÖÜäöüß]+)*")


def keyword_boosts(chunk_text: str, question_text: str) -> list[str]:
    """Deepgram `keywords` boost list for one round-1 comprehension chunk.

    Tokenizes the chunk's own interviewer text plus the brief's question
    text (hyphen-split, case-insensitive) and keeps only the tokens whose
    lowercased form is in the curated _TECH_LOANWORDS allowlist above --
    biasing the recognizer toward THIS chunk's tech vocabulary specifically
    (not its generic German words or proper nouns), so a loanword like
    "Frontend" or "Vibe-Coding" is more likely to survive intact instead of
    being misheard.

    Case-insensitive de-dupe, capped at 20 terms, each getting a moderate
    boost intensifier ("term:2") in Deepgram's `word:intensifier` format.
    The emitted surface form always comes from _TECH_LOANWORD_CASING (for
    the entries that need it) or str.capitalize() otherwise -- the
    corpus-typical capitalized form for that term -- regardless of how the
    token happens to be cased in this particular chunk's text.
    """
    combined = f"{chunk_text or ''} {question_text or ''}"
    seen: dict[str, str] = {}
    for raw in _KEYWORD_TOKEN_RE.findall(combined):
        for part in re.split(r"[-‐‑]", raw):
            key = part.lower()
            if key not in _TECH_LOANWORDS or key in seen:
                continue
            seen[key] = _TECH_LOANWORD_CASING.get(key, part.capitalize())
    return [f"{term}:2" for term in list(seen.values())[:20]]


async def transcribe_comprehension(audio: bytes, content_type: Optional[str], keywords: list[str]) -> str:
    """Round-1 retell transcription: the same Deepgram prerecorded call as
    transcribe_answer above (nova-2, de, punctuate, smart_format -- nova-2
    is required here, not nova-3, since nova-3's `keyterm` boosting is
    English-only and the learner's retell is German), plus a per-request
    `keywords` boost list (see keyword_boosts) biasing toward the terms
    this specific chunk is likely to contain. Fails safe: any error with
    the keyworded request (unsupported param, timeout, ...) is logged and
    the call is retried once WITHOUT keywords -- keyword boosting must
    never be the reason round 1 breaks.
    """
    headers = {
        "Authorization": f"Token {deepgram_api_key}",
        "Content-Type": content_type or "audio/webm",
    }
    # httpx's `params=` REPLACES a URL's existing query string rather than
    # merging into it -- passing just `keywords` here would silently drop
    # `language=de` and let Deepgram fall back to an English model on
    # German audio. Derive the full param list from _DEEPGRAM_ANSWER_URL
    # (single source of truth for model/language/punctuate/smart_format)
    # and append `keywords` on top of it instead.
    split_url = urlsplit(_DEEPGRAM_ANSWER_URL)
    endpoint = f"{split_url.scheme}://{split_url.netloc}{split_url.path}"
    params = parse_qsl(split_url.query) + [("keywords", kw) for kw in keywords]
    if keywords:
        logger.info(f"comprehension transcription keyword boosts: {keywords}")
    try:
        # A failed keyworded attempt closes its span with the exception
        # recorded (status ERROR), and the no-keyword retry below opens a
        # fresh sibling `stt` span — so a boost-triggered retry shows up in
        # the trace as two Deepgram calls, which it was.
        with generation_span(
            "stt",
            system="deepgram",
            model="nova-2",
            operation="transcription",
            input_text=f"[{len(audio)} bytes, {content_type or 'audio/webm'}]",
        ) as span:
            span.set_attribute("audio.bytes", len(audio))
            if keywords:
                span.set_attribute("stt.keywords", ", ".join(keywords))
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(endpoint, headers=headers, params=params, content=audio)
                resp.raise_for_status()
                body = resp.json()
            transcript = body["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
            record_generation_output(span, transcript)
            return transcript
    except Exception as exc:  # noqa: BLE001 -- a keyword-boost failure must never break round 1
        if not keywords:
            raise
        logger.warning(f"comprehension transcription with keyword boosts failed ({exc}); retrying without")
        return await transcribe_comprehension(audio, content_type, [])
