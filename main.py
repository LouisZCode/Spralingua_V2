# Backend:  uvicorn main:app --host 0.0.0.0 --port 8765
# Frontend: cd frontend && npm run dev

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from agents.load_prompts import load_prompts
from config import database_url
from database import ActivitySession, dispose_engine, get_sessionmaker, init_engine
from pipeline import run_pipeline
from pipeline.factory import ACTIVE_TASKS


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail-loud: if Postgres is unreachable, init_engine raises here and
    # uvicorn exits non-zero. Saves us from silent broken-persistence builds.
    await init_engine(database_url)
    yield
    await dispose_engine()


app = FastAPI(lifespan=lifespan)

# Frontend dev server lives on :3000; WebSocket isn't subject to CORS, but
# the /say HTTP endpoint is. Keep this explicit (no wildcard) for clarity.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/lessons/{lesson_id}")
def lesson_meta(lesson_id: str):
    """Briefing copy + title (+ optional completion content) for the conversation page.

    Loader already falls back to `lesson_zero` on unknown id (with a logged
    warning), so the frontend never gets a 404 here. ``completion`` may be
    ``None`` — the frontend's ``SessionSummaryModal`` supplies defaults.
    """
    lesson = load_prompts(lesson_id)
    return {
        "title": lesson["title"],
        "briefing": lesson["briefing"],
        "completion": lesson.get("completion"),
    }


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Return the activity_session row for the post-session modal to render.

    ``ended_at IS NULL`` means the disconnect-side finalize step hasn't run
    yet (evaluators + DB update happen in pipeline/factory.py's finally:
    block, after the WS is already closed). The frontend polls this route
    every ~1s until ``ended_at`` is set, then renders the eval blocks.
    """
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id")

    async with get_sessionmaker()() as db:
        row = await db.get(ActivitySession, session_uuid)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "id": str(row.id),
        "lesson_id": row.lesson_id,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "ended_by": row.ended_by,
        "passed": row.passed,
        "goal_eval": row.goal_eval,
        "pron_eval": row.pron_eval,
    }


@app.websocket("/ws/{user_id}")
async def ws_endpoint(
    websocket: WebSocket,
    user_id: str,
    voice: str = "happy_harry",
    lesson: str = "lesson_zero",
):
    await websocket.accept()
    await run_pipeline(websocket, user_id, voice, lesson)


class SayBody(BaseModel):
    text: str


@app.post("/say/{user_id}")
async def say(user_id: str, body: SayBody):
    """Inject a typed turn into an active pipeline.

    LangchainProcessor accepts LLMContextFrame directly — it extracts the last
    message's content and runs the chain. For a complete typed utterance we
    don't need STT/VAD/buffer at all; we just hand the LLM stage the same frame
    type the converter would have produced. The converter passes it through
    unchanged via its else branch. TTS / audio playback / Langfuse / goodbye
    detection / exchange count all fire identically to a spoken turn.
    """
    task = ACTIVE_TASKS.get(user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="No active session for that user_id")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")

    context = LLMContext([{"role": "user", "content": text}])
    await task.queue_frame(LLMContextFrame(context=context))
    return {"ok": True}
