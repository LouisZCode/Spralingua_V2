"""
Session Logger for Pipecat Pipeline

Intercepts pipecat debug logs to extract timing and transcription.
Writes clean, human-readable session logs with macro + micro timing.
"""

import re
from datetime import datetime
from pathlib import Path


class SessionLogger:
    """
    Logs voice conversation sessions by intercepting pipecat debug messages.

    Tracks per-turn:
    - MACRO: Total latency (user stopped → audio started)
    - MICRO: STT, LLM, TTS breakdown
    - User and agent text
    """

    def __init__(self, log_dir: str = "logs/conversations"):
        self._base_dir = Path(log_dir)
        self._session_start = datetime.now()

        # Daily folder
        today = self._session_start.strftime("%Y-%m-%d")
        self._day_dir = self._base_dir / today
        self._day_dir.mkdir(parents=True, exist_ok=True)

        # Auto-increment session number
        existing = list(self._day_dir.glob("session_*.log"))
        self._session_num = len(existing) + 1
        self._session_id = f"session_{self._session_num:03d}"

        # Log file
        self._log_file = self._day_dir / f"{self._session_id}.log"
        self._file = open(self._log_file, "w", encoding="utf-8")

        # Markdown transcript file
        self._transcript_file = self._day_dir / f"{self._session_id}.md"
        self._md_file = open(self._transcript_file, "w", encoding="utf-8")
        self._system_prompt_written = False

        # Turn timing trackers
        self._user_started_ts = None
        self._user_stopped_ts = None
        self._transcription_ts = None
        self._tts_started_ts = None
        self._bot_started_ts = None

        # Turn content trackers
        self._user_text = None
        self._agent_text = None

    def write_header(self, config: dict = None):
        """Write session header with config. Call AFTER services are created."""
        self._file.write("=" * 70 + "\n")
        self._file.write(f"SESSION START: {self._session_start.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self._file.write("=" * 70 + "\n\n")

        if config:
            for service, params in config.items():
                if isinstance(params, dict):
                    params_str = ", ".join(f"{k}={v}" for k, v in params.items())
                else:
                    params_str = str(params)
                self._file.write(f"[CONFIG] {service}: {params_str}\n")
            self._file.write("\n")

        self._file.write("--- CONVERSATION ---\n\n")
        self._file.flush()

    def write_system_prompt(self, prompt: str):
        """Write system prompt to markdown transcript (call once, first turn)."""
        if self._system_prompt_written:
            return
        self._md_file.write(f"# {self._session_id.replace('_', ' ').title()} - {self._session_start.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        self._md_file.write("## System Prompt\n```\n")
        self._md_file.write(prompt)
        self._md_file.write("\n```\n\n## Conversation\n\n")
        self._md_file.flush()
        self._system_prompt_written = True

    def _write(self, message: str):
        """Write a line to log file."""
        self._file.write(message + "\n")
        self._file.flush()

    @property
    def session_dir(self) -> Path:
        """Return the daily session directory."""
        return self._day_dir

    @property
    def session_id(self) -> str:
        """Return the session ID (e.g., 'session_001')."""
        return self._session_id

    def _format_duration(self, seconds: int) -> str:
        """Format duration smartly: 15s, 3m 40s, 1h 2m 0s"""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            mins = seconds // 60
            secs = seconds % 60
            return f"{mins}m {secs}s"
        else:
            hours = seconds // 3600
            mins = (seconds % 3600) // 60
            secs = seconds % 60
            return f"{hours}h {mins}m {secs}s"

    # --- Event handlers called from loguru sink ---

    def on_user_started_speaking(self):
        """Called when VAD detects user started speaking."""
        self._user_started_ts = datetime.now()

    def on_user_stopped_speaking(self):
        """Called when VAD detects user stopped speaking."""
        self._user_stopped_ts = datetime.now()

    def on_transcription(self, text: str):
        """Called when STT produces transcription (LLM starts)."""
        self._transcription_ts = datetime.now()
        self._user_text = text

    def on_llm_response(self, text: str):
        """Called when LLM response chunk is sent to TTS."""
        # Only capture first TTS chunk timestamp (marks LLM done)
        if self._tts_started_ts is None:
            self._tts_started_ts = datetime.now()
        # Concatenate chunks to build full response
        if self._agent_text is None:
            self._agent_text = text
        else:
            self._agent_text += " " + text

    def on_bot_started_speaking(self):
        """Called when audio playback starts."""
        self._bot_started_ts = datetime.now()

    def on_bot_stopped_speaking(self):
        """Called when TTS audio playback finished."""
        self._write_turn_summary()
        self._reset_turn()

    def _write_turn_summary(self):
        """Write formatted turn summary with timing breakdown."""
        if not self._user_stopped_ts or not self._bot_started_ts:
            return  # Incomplete turn

        # Calculate timings
        macro = (self._bot_started_ts - self._user_stopped_ts).total_seconds()
        stt = (self._transcription_ts - self._user_stopped_ts).total_seconds() if self._transcription_ts else 0
        llm = (self._tts_started_ts - self._transcription_ts).total_seconds() if self._tts_started_ts and self._transcription_ts else 0
        tts = (self._bot_started_ts - self._tts_started_ts).total_seconds() if self._tts_started_ts else 0

        # Format output
        time_str = self._bot_started_ts.strftime("%H:%M:%S")
        self._write(f"[{time_str}] TURN LATENCY: {macro:.1f}s (user stopped -> audio started)")
        self._write(f"           ├─ STT:    {stt:.1f}s")
        self._write(f"           ├─ LLM:    {llm:.1f}s")
        self._write(f"           └─ TTS:    {tts:.1f}s")

        # Truncate long text
        user_display = (self._user_text[:80] + "...") if self._user_text and len(self._user_text) > 80 else self._user_text
        agent_display = (self._agent_text[:80] + "...") if self._agent_text and len(self._agent_text) > 80 else self._agent_text

        if user_display:
            self._write(f"           User: \"{user_display}\"")
        if agent_display:
            self._write(f"           Agent: \"{agent_display}\"")
        self._write("")  # Blank line between turns

        # Write to markdown transcript (full text, not truncated)
        if self._user_text:
            self._md_file.write(f"User: {self._user_text}\n\n")
        if self._agent_text:
            self._md_file.write(f"Harry: {self._agent_text}\n\n---\n\n")
        self._md_file.flush()

    def _reset_turn(self):
        """Reset all turn tracking variables."""
        self._user_started_ts = None
        self._user_stopped_ts = None
        self._transcription_ts = None
        self._tts_started_ts = None
        self._bot_started_ts = None
        self._user_text = None
        self._agent_text = None

    def close(self):
        """Close session and write footer."""
        session_end = datetime.now()
        duration = session_end - self._session_start
        duration_str = self._format_duration(int(duration.total_seconds()))

        self._file.write("\n" + "=" * 70 + "\n")
        self._file.write(f"SESSION END: {session_end.strftime('%Y-%m-%d %H:%M:%S')} | Duration: {duration_str}\n")
        self._file.write("=" * 70 + "\n")
        self._file.close()

        self._md_file.close()

        print(f"Session log saved to: {self._log_file}")
        print(f"Transcript saved to: {self._transcript_file}")


def setup_session_logger(stt, tts, llm_model: str, log_dir: str = "logs/conversations"):
    """
    Create and configure session logger from service objects.
    Extracts config dynamically from STT and TTS services.

    Args:
        stt: DeepgramSTTService instance
        tts: MiniMaxHttpTTSService instance
        llm_model: LLM model name string
        log_dir: Directory for log files

    Returns:
        SessionLogger instance (already configured with loguru sink)
    """
    from loguru import logger

    session_logger = SessionLogger(log_dir=log_dir)
    session_logger.write_header({
        "deepgram": {
            "language": stt._settings.get("language"),
            "model": stt._settings.get("model"),
        },
        "minimax": {
            "model": tts._model_name,
            "voice_id": tts._voice_id,
            "language": tts._settings.get("language_boost"),
            "speed": tts._settings.get("voice_setting", {}).get("speed"),
        },
        "llm": {"model": llm_model},
    })
    logger.add(create_pipecat_log_sink(session_logger), filter="pipecat")

    return session_logger


def create_pipecat_log_sink(session_logger: SessionLogger):
    """
    Creates a loguru sink that intercepts pipecat logs and routes to SessionLogger.

    Usage:
        logger.add(create_pipecat_log_sink(session_logger), filter="pipecat")
    """
    def sink(message):
        text = message.record["message"]

        if "User started speaking" in text:
            session_logger.on_user_started_speaking()

        elif "User stopped speaking" in text:
            session_logger.on_user_stopped_speaking()

        elif "Invoking chain with" in text:
            match = re.search(r"Invoking chain with (.+)$", text)
            if match:
                session_logger.on_transcription(match.group(1))

        elif "Generating TTS" in text:
            match = re.search(r"Generating TTS \[(.+)\]$", text)
            if match:
                session_logger.on_llm_response(match.group(1))

        elif "Bot started speaking" in text:
            session_logger.on_bot_started_speaking()

        elif "Bot stopped speaking" in text:
            session_logger.on_bot_stopped_speaking()

    return sink
