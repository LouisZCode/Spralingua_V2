# Spralingua v2

Real-time voice conversation agent for language learning.

## Architecture

```
Microphone → STT (Deepgram) → LLM (LangChain) → TTS (MiniMax) → Speaker
                                    ↓
                            AudioBufferProcessor
                                    ↓
                          logs/conversations/
```

| Component | Provider |
|-----------|----------|
| STT | Deepgram (Nova-2) |
| LLM | OpenAI (gpt-4o-mini) via LangChain |
| TTS | MiniMax |
| VAD | Silero |
| Pipeline | Pipecat |

## Setup

### Requirements

- Python 3.12+
- ffmpeg (for MP3 conversion)
- PortAudio (for microphone access)

### Installation

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Add your API keys to .env
```

### Environment Variables

```
OPENAI_API_KEY=
DEEPGRAM_API_KEY=
MINIMAX_API_KEY=
MINIMAX_GROUP_ID=
```

## Usage

```bash
python main.py
```

Speak into your microphone. Press `Ctrl+C` to end the session.

## Output

Sessions are saved to `logs/conversations/YYYY-MM-DD/`:

```
logs/conversations/2026-01-04/
├── session_001.log    # Timing metrics
├── session_001.mp3    # Conversation audio
├── session_002.log
├── session_002.mp3
└── ...
```

## Project Structure

```
├── main.py                 # Entry point
├── pipeline/
│   ├── factory.py          # Pipeline construction
│   └── converters.py       # VAD-gated transcription buffering
├── agents/
│   ├── conversation_agent.py  # LangChain agent factory
│   ├── pipecat_wrapper.py     # Pipecat ↔ LangChain adapter
│   └── prompts/               # One YAML per lesson
├── services/
│   ├── stt.py              # Deepgram STT config
│   ├── tts.py              # MiniMax TTS config
│   └── transport.py        # Local audio transport + VAD
├── logs/
│   └── session_logger.py   # Timing metrics + session management
└── config/
    └── settings.py         # Environment variables
```
