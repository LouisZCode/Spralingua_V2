# Spralingua v2

Real-time, browser-based voice conversation agent for language learning. A Next.js frontend streams microphone audio over a WebSocket to a FastAPI backend that runs a per-client Pipecat pipeline (STT → VAD-gated buffering → LLM → TTS) and streams agent speech back to the browser. Each connection gets its own isolated pipeline; multiple users can talk to the agent concurrently.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full architectural breakdown, ADRs, and tech debt.

## Architecture (one-line view)

```
Browser (mic) ⇄ WebSocket ⇄ FastAPI / Pipecat (STT → buffer → LLM → TTS) ⇄ Browser (speaker)
                                              ↓
                                      logs/conversations/YYYY-MM-DD/{session_id}.mp3
```

## Stack

| Component | Technology |
|---|---|
| STT | Deepgram nova-3 (per-lesson language, smart_format, optional keyterm prompting) |
| LLM | Cerebras `gpt-oss-120b` via OpenRouter (`ChatOpenAI` + LangGraph, `provider.order=["cerebras"]`) |
| TTS | MiniMax `speech-2.8-turbo` (dynamic voice via `VOICE_MAP`) |
| VAD | Silero (1.5s silence threshold) |
| Pipeline | Pipecat 0.0.98 |
| Backend | FastAPI + uvicorn, Python 3.12 |
| Frontend | Next.js 16 + React 19 + Tailwind CSS 4 + TypeScript |
| Observability | Langfuse v4 via OpenTelemetry — OTLP ingestion (`x-langfuse-ingestion-version: 4`); one trace per turn plus one-shot traces for every drill/judge call |

## Setup

### Requirements

- Python 3.12+ (managed via [`uv`](https://docs.astral.sh/uv/))
- Node.js 20+
- `ffmpeg` (used by `pydub` for WAV → MP3 conversion)

### Install

```bash
# Backend
uv sync

# Frontend
cd frontend && npm install
```

### Environment variables (`.env` at repo root)

```
DEEPGRAM_API_KEY=
MINIMAX_API_KEY=
MINIMAX_GROUP_ID=
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=           # optional, defaults to https://openrouter.ai/api/v1
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_BASE_URL=
LANGFUSE_TRACING_ENVIRONMENT=  # optional, defaults to "dev"
```

## Run

```bash
# Terminal 1 — backend (FastAPI + per-client Pipecat pipelines on :8765)
uvicorn main:app --host 0.0.0.0 --port 8765

# Terminal 2 — frontend (Next.js dev server on :3000)
cd frontend && npm run dev
```

Open `http://localhost:3000`, pick a lesson + voice, click *Continue* → *I am ready*, allow microphone, and start talking.

## Output

Each session writes one file under `logs/conversations/YYYY-MM-DD/`:

| File | Content |
|---|---|
| `{session_id}.mp3` | Full conversation audio (WAV captured by `AudioBufferProcessor`, converted via `pydub`), named by the session's `activity_session` id |

Transcript and evaluation results are stored in Postgres (`activity_session`); per-turn STT / LLM / TTS spans land in Langfuse for latency, token, and trace analytics.

## Project structure

```
├── main.py                     FastAPI app — /health, /lessons/{lesson_id}, /ws/{user_id}, /say/{user_id}
├── config/settings.py          Loads .env (Deepgram, MiniMax, OpenRouter, Langfuse)
├── services/
│   ├── stt.py                  stt_deepgram() — Deepgram nova-3 config (per-lesson language + keyterms)
│   ├── tts.py                  tts_minimax(session, voice) — MiniMax + VOICE_MAP
│   └── transport.py            FastAPI WebSocket transport factory (current) + legacy local/WS variants
├── agents/
│   ├── conversation_agent.py   agent_assembly(user_id) — ChatOpenAI via OpenRouter (Cerebras-pinned) + InMemorySaver
│   ├── pipecat_wrapper.py      ClientWrapper — Pipecat ↔ LangChain adapter; owns the per-turn `llm` generation span
│   ├── dynamic_prompts.py      Context + StudentProfile dataclasses
│   ├── conversational_prompt.py  layered_prompt_middleware — branches on YAML `type` (conversation | respond)
│   ├── load_prompts.py         load_prompts(lesson_id) — resolves agents/prompts/{lesson_id}.yaml
│   ├── observability.py        OTel TracerProvider + OTLP exporter → Langfuse (ingestion v4) + generation_span helpers
│   └── prompts/                One YAML per lesson (lesson_zero, a1_l1, goodbye_test)
├── pipeline/
│   ├── factory.py              run_pipeline() — builds & runs per-client pipeline
│   ├── converters.py           TranscriptionToContextConverter — VAD-gated buffering
│   ├── observers.py            PipelineLatencyObserver — per-turn root trace spans (turn-{N}-{lesson})
│   └── tts_duration.py         TTSDurationTracker — per-turn TTS audio length metric
└── frontend/src/components/
    ├── VoiceChat.tsx           Thin orchestrator
    ├── SetupView.tsx           Lesson / level / situation / voice picker
    ├── ConversationView.tsx    Briefing → live chat → summary
    └── SessionSummaryModal.tsx Post-session modal driven by YAML `completion:` block
```

## Notes

- There is no auth and no reconnection logic; `user_id="0001"` is hardcoded in the frontend and `agents/fake_profiles.py` while the prompt + LLM layers are being iterated. The user model moves to a DB once the prompt design is settled.
- No Python test suite. Smoke-test changes by running the two dev commands above and walking through a lesson end-to-end.
