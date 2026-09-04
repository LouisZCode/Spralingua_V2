# Spralingua v2

Spralingua teaches German. Learners reach the product from `/practice`, which offers four modes today — Satzschmiede (vocabulary practice), the Flow (a mixed stream of the single-grammar drills), tandem partners (Lena and Paul, voice conversation), and Briefkasten (a letter-writing exercise) — plus Clara, a voice grammar teacher, on paid tiers. Most of the app is ordinary FastAPI HTTP drill routers; the piece that started the project — a real-time voice conversation agent — is still the spine for anything spoken: a Next.js frontend streams microphone audio over a WebSocket to a FastAPI backend that runs a per-client Pipecat pipeline (STT → VAD-gated buffering → LLM → TTS) and streams agent speech back to the browser. Each connection gets its own isolated pipeline; multiple users can talk to the agent concurrently.

Auth is Google sign-in (a session JWT gates the WebSocket and every API route). Billing runs through Stripe (`payments/`) and a coin economy (`coins/`) that gates every priced action behind daily allowances, purchased top-ups, and a signup grant. Error reporting (Sentry) and tracing (Langfuse) are both wired in and DSN/key-gated — absent, they're inert and the app runs the same.

`ARCHITECTURE.md` (the full architectural breakdown, ADRs, tech debt) and `CLAUDE.md` (day-to-day conventions) are maintained on the developer's machine and are gitignored — they are not in this repo.

## Architecture (one-line view)

```
Browser (mic) ⇄ WebSocket ⇄ FastAPI / Pipecat (STT → buffer → LLM → TTS) ⇄ Browser (speaker)
                                              ↓
                                      logs/conversations/YYYY-MM-DD/{session_id}.mp3
```

Everything else in the product — Satzschmiede, the Flow, Briefkasten, the standalone grammar drills, billing, auth, stats — is plain HTTP routers around Postgres and one-shot judge LLM calls; they don't touch the pipeline above.

## Stack

| Component | Technology |
|---|---|
| STT | Deepgram nova-3 (per-lesson language, smart_format, optional keyterm prompting) |
| LLM | Cerebras `gpt-oss-120b` via OpenRouter (`ChatOpenAI` + LangGraph/LangChain, `provider.order=["cerebras"]`); one-shot judge/evaluator calls go Cerebras-direct with an OpenRouter fallback |
| TTS | MiniMax `speech-2.8-turbo` (dynamic voice via `VOICE_MAP`) |
| VAD | Silero (1.5s silence threshold) |
| Pipeline | Pipecat 0.0.98 |
| Backend | FastAPI + uvicorn, Python 3.12 |
| Frontend | Next.js 16 + React 19 + Tailwind CSS 4 + TypeScript |
| Database | Postgres (async via SQLAlchemy/asyncpg; migrations via Alembic) |
| Billing | Stripe (Checkout, customer portal, webhook-driven tier) |
| Observability | Langfuse v4 via OpenTelemetry (one trace per turn, plus one-shot traces per drill/judge call); Sentry for error reporting — both optional, key-gated |

## Setup

### Requirements

- Python 3.12+ (managed via [`uv`](https://docs.astral.sh/uv/))
- Node.js 20+
- Postgres (the backend fails loud at startup if it can't reach `DATABASE_URL`)
- `ffmpeg` (used by `pydub` for WAV → MP3 conversion)
- A Google OAuth web client id, for sign-in

### Install

```bash
# Backend
uv sync

# Frontend
cd frontend && npm install
```

### Environment variables

Backend: copy `.env.example` to `.env` at the repo root and fill it in — Postgres, Deepgram, MiniMax, OpenRouter and Google sign-in/JWT are required; Langfuse, Azure Speech (pronunciation), Sentry, and the demo rate-limit knobs are all optional and degrade gracefully when unset. Cerebras and Stripe keys aren't in `.env.example` yet but are read by `config/settings.py`; see `CLAUDE.md`'s environment table for the full list.

Frontend: copy `frontend/.env.example` to `frontend/.env.local`. `NEXT_PUBLIC_SENTRY_DSN` is the only one that matters locally, and it's optional.

## Run

```bash
# Terminal 1 — backend (FastAPI + per-client Pipecat pipelines on :8765; needs Postgres reachable)
uvicorn main:app --host 0.0.0.0 --port 8765

# Terminal 2 — frontend (Next.js dev server on :3000)
cd frontend && npm run dev
```

Open `http://localhost:3000`, sign in with Google, and go to `/practice`.

## Output

Each voice session writes one file under `logs/conversations/YYYY-MM-DD/`:

| File | Content |
|---|---|
| `{session_id}.mp3` | Full conversation audio (WAV captured by `AudioBufferProcessor`, converted via `pydub`), named by the session's `activity_session` id |

Transcript, evaluation results, and every drill/coin/billing event are stored in Postgres; per-turn STT / LLM / TTS spans and one-shot judge calls land in Langfuse for latency, token, and trace analytics.

## Project structure

```
├── main.py                     FastAPI app — health check, lesson/session routes, /ws/{user_id}, /say/{user_id}
├── config/settings.py          Loads .env (Deepgram, MiniMax, OpenRouter/Cerebras, Langfuse, DB, auth, Stripe, Sentry)
├── auth/                       Google sign-in + session JWT (AUTH-001)
├── coins/                      The entitlement layer — prices, gates, ledger (PAY-002)
├── payments/                   Stripe billing — Checkout, portal, webhook (PAY-001)
├── services/
│   ├── stt.py                  stt_deepgram() — Deepgram nova-3 config (per-lesson language + keyterms)
│   └── tts.py                  tts_minimax(session, voice) — MiniMax + VOICE_MAP
├── agents/
│   ├── conversation_agent.py   agent_assembly(user_id) — ChatOpenAI via OpenRouter (Cerebras-pinned) + InMemorySaver
│   ├── pipecat_wrapper.py      ClientWrapper — Pipecat ↔ LangChain adapter; owns the per-turn `llm` generation span
│   ├── conversational_prompt.py  layered_prompt_middleware — branches on YAML `type` (conversation | respond | tandem | teacher)
│   ├── load_prompts.py         load_prompts(lesson_id) — resolves agents/prompts/{lesson_id}.yaml
│   ├── openrouter_llm.py       structured_judge_llm() — the factory every one-shot judge/evaluator builds through
│   ├── observability.py        OTel TracerProvider + OTLP exporter → Langfuse (ingestion v4) + generation_span helpers
│   └── prompts/                One YAML per lesson/persona (lesson_zero, tandem, teacher, ...)
├── pipeline/
│   ├── factory.py              run_pipeline() — builds & runs per-client pipeline
│   ├── converters.py           TranscriptionToContextConverter — VAD-gated buffering
│   ├── observers.py            PipelineLatencyObserver — per-turn root trace spans
│   └── tts_duration.py         TTSDurationTracker — per-turn TTS audio length metric
├── satz/ bauteil/ sprechen/ verbformen/ verbindungen/ zeitfaerbung/ genus/ faelle/ satzbau/ szenario/ briefkasten/ interview/
│                                One HTTP router package per drill/exercise; all share the judge factory + the user_errors ledger
├── grammar/                     The 33-pattern taxonomy every drill and the tandem debrief read from and write to
└── frontend/src/components/
    ├── PracticeMenu.tsx         /practice hub — the four real modes plus Clara
    ├── VoiceChat.tsx / TandemChat.tsx / TeacherChat.tsx   Thin orchestrators around ConversationView
    ├── ConversationView.tsx    Briefing → live chat → summary; the shared voice-session component
    └── SessionSummaryModal.tsx Post-session modal driven by YAML `completion:` block
```

This is a map, not the full picture — see `CLAUDE.md` for the conventions and `ARCHITECTURE.md` for the complete breakdown.

## Notes

- No Python test suite. Smoke-test changes by running the two dev commands above and walking through a session end-to-end; the tandem partner has a dedicated simulated-student harness (`sim/chat.py`) — see `CLAUDE.md`.
- Auth is real (Google sign-in + a session JWT); there's no anonymous access except the rate-limited front-page demo socket (`/ws/demo/{user_id}`).
