# Backend:  uvicorn main:app --host 0.0.0.0 --port 8765 --reload
# Frontend: cd frontend && npm run dev

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from pipeline import run_pipeline
from pipeline.factory import ACTIVE_TASKS

app = FastAPI()

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


@app.websocket("/ws/{user_id}")
async def ws_endpoint(
    websocket: WebSocket,
    user_id: str,
    level: str = "A1",
    situation: str = "introducing_yourself",
    voice: str = "happy_harry",
):
    await websocket.accept()
    await run_pipeline(websocket, user_id, level, situation, voice)


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
