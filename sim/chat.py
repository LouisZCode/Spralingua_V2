"""Tandem conversation simulator (TAND-002 calibration harness).

Drives a REAL local pipeline session over text only: a background `hold`
process keeps the WebSocket open (audio frames are received and discarded),
and each student turn goes in through `POST /say/{user_id}` — which the
endpoint guarantees is identical to a spoken turn (TTS, Langfuse, goodbye
detection, exchange count all fire). STT is skipped entirely; TTS still
synthesizes and streams at real-time pace, so a turn completes when the bot
"finishes speaking" into the discarding holder.

Lena's replies are recovered from the BACKEND LOG (the uvicorn process must
be started with its output redirected to a file; pass it via --backend-log
or SIM_BACKEND_LOG). The session .md transcript can't be used: typed /say
turns have no VAD timestamps, so `session_logger._write_turn_summary`
discards them as incomplete turns and they never land in the .md.

Usage (from repo root, backend running on :8765 with logs to a file):
    uv run python sim/chat.py start --topic "Der Alltag"
    uv run python sim/chat.py say "Hallo Lena!"
    uv run python sim/chat.py transcript
    uv run python sim/chat.py stop

`say` prints Lena's reply (or SESSION_ENDED if the pipeline closed — e.g.
goodbye detection fired or the exchange cap hit). The student speaks first:
there is no auto-greeting kick on connect.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUN_DIR = Path(__file__).resolve().parent / ".run"
STATE = RUN_DIR / "state.json"
BASE = os.environ.get("SIM_BASE", "http://127.0.0.1:8765")
WS_BASE = BASE.replace("http", "ws", 1)

_INVOKE_RE = re.compile(r"Invoking chain with (.+)$", re.MULTILINE)
_TTS_RE = re.compile(r"Generating TTS \[(.+)\]$", re.MULTILINE)
_BOT_DONE = "Bot stopped speaking"


def _load_state() -> dict:
    if not STATE.exists():
        sys.exit("no active sim session — run `start` first")
    return json.loads(STATE.read_text())


def _save_state(state: dict) -> None:
    RUN_DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_hold(args: argparse.Namespace) -> None:
    """Keep the WS open; discard everything the server sends."""
    import asyncio

    import websockets

    async def run() -> None:
        async with websockets.connect(
            args.url, max_size=None, ping_interval=20, close_timeout=5
        ) as ws:
            Path(args.ready_file).write_text("ok")
            try:
                async for _ in ws:
                    pass  # audio + RTVI frames, all discarded
            except websockets.ConnectionClosed:
                pass

    asyncio.run(run())


def cmd_start(args: argparse.Namespace) -> None:
    backend_log = Path(args.backend_log or os.environ.get("SIM_BACKEND_LOG", ""))
    if not backend_log.is_file():
        sys.exit("--backend-log (or SIM_BACKEND_LOG) must point at the uvicorn log file")

    # Kill any stale holder from a previous run.
    if STATE.exists():
        old = json.loads(STATE.read_text())
        if _alive(old.get("pid", -1)):
            os.kill(old["pid"], signal.SIGTERM)
            time.sleep(1.0)
        STATE.unlink()

    sys.path.insert(0, str(REPO))
    from auth.tokens import issue_session_jwt  # same JWT_SECRET as the server

    token = issue_session_jwt(args.user)
    qs = urllib.parse.urlencode(
        {"lesson": args.lesson, "topic": args.topic, "voice": args.voice, "token": token}
    )
    url = f"{WS_BASE}/ws/{args.user}?{qs}"

    RUN_DIR.mkdir(exist_ok=True)
    ready = RUN_DIR / "ready"
    ready.unlink(missing_ok=True)
    holder_log = open(RUN_DIR / "holder.log", "w")
    proc = subprocess.Popen(
        [sys.executable, __file__, "hold", "--url", url, "--ready-file", str(ready)],
        stdout=holder_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    deadline = time.time() + 20
    while time.time() < deadline and not ready.exists():
        if proc.poll() is not None:
            sys.exit(
                "holder died before connecting — is the backend running on :8765? "
                f"see {RUN_DIR / 'holder.log'}"
            )
        time.sleep(0.2)
    if not ready.exists():
        proc.terminate()
        sys.exit("timed out waiting for the WebSocket to connect")

    time.sleep(1.5)  # let the pipeline finish assembling before the first /say
    _save_state(
        {
            "pid": proc.pid,
            "user": args.user,
            "token": token,
            "topic": args.topic,
            "backend_log": str(backend_log),
            "offset": backend_log.stat().st_size,
            "conversation": [],
        }
    )
    print(f"session started (lesson: {args.lesson!r}, topic: {args.topic!r}, user: {args.user})")
    print('you speak first — send a greeting with: say "Hallo!"')


def _read_new(state: dict) -> str:
    with open(state["backend_log"], encoding="utf-8", errors="replace") as f:
        f.seek(state["offset"])
        return f.read()


def cmd_say(args: argparse.Namespace) -> None:
    state = _load_state()
    payload = json.dumps({"text": args.text}).encode()

    for attempt in range(3):
        req = urllib.request.Request(
            f"{BASE}/say/{state['user']}",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {state['token']}",
            },
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(2.2)  # per-user /say interval is 2s
                continue
            if e.code == 404:
                print("SESSION_ENDED (no active pipeline — goodbye or cap reached)")
                return
            sys.exit(f"/say failed: HTTP {e.code} {e.read().decode()[:200]}")
        except urllib.error.URLError as e:
            sys.exit(f"/say failed: {e}")

    def reply_from(chunk: str) -> str | None:
        """Full reply once the bot finished speaking it; None while pending."""
        invokes = list(_INVOKE_RE.finditer(chunk))
        if not invokes:
            return None
        tail = chunk[invokes[-1].end():]
        if _BOT_DONE not in tail:
            return None
        spoken = tail[: tail.index(_BOT_DONE)]
        parts = [m.group(1) for m in _TTS_RE.finditer(spoken)]
        return " ".join(parts).strip() if parts else None

    deadline = time.time() + 120
    while time.time() < deadline:
        chunk = _read_new(state)
        reply = reply_from(chunk)
        holder_up = _alive(state["pid"])
        if reply is None and not holder_up:
            time.sleep(2.0)  # teardown may still be flushing log lines
            chunk = _read_new(state)
            reply = reply_from(chunk)
            if reply is None:
                # Last resort: TTS chunks were generated but "Bot stopped
                # speaking" never logged before the pipeline closed.
                parts = [m.group(1) for m in _TTS_RE.finditer(chunk)]
                reply = " ".join(parts).strip() if parts else None
            if reply:
                state["offset"] += len(chunk.encode())
                state["conversation"] += [("STUDENT", args.text), ("PARTNER", reply)]
                _save_state(state)
                print(reply)
            print("SESSION_ENDED (connection closed)")
            return
        if reply is not None:
            done_at = chunk.index(_BOT_DONE) + len(_BOT_DONE)
            state["offset"] += len(chunk[:done_at].encode())
            state["conversation"] += [("STUDENT", args.text), ("PARTNER", reply)]
            _save_state(state)
            print(reply)
            # Graceful-end marker: the wrapper logs [END] when goodbye/cap
            # detection fires; the pipeline closes right after this reply.
            end = re.search(r"\[END\] Pending pipeline close \(([^)]+)\)", chunk)
            if end:
                print(f"SESSION_ENDING ({end.group(1)})")
            return
        time.sleep(0.5)
    sys.exit("timed out waiting for Lena's reply (120s)")


def cmd_transcript(_args: argparse.Namespace) -> None:
    state = _load_state()
    for speaker, text in state["conversation"]:
        print(f"{speaker}: {text}\n")


def cmd_stop(_args: argparse.Namespace) -> None:
    state = _load_state()
    if _alive(state["pid"]):
        os.kill(state["pid"], signal.SIGTERM)
        time.sleep(1.0)
    print(f"stopped ({len(state['conversation']) // 2} exchanges)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="open a tandem session (holder in background)")
    s.add_argument("--topic", required=True)
    s.add_argument("--user", default="0001")
    s.add_argument("--voice", default="German_Female")
    # TAND-008: pick the partner — "tandem" (Lena) or "tandem_paul" (Paul).
    s.add_argument("--lesson", default="tandem")
    s.add_argument("--backend-log", default=None)
    s.set_defaults(fn=cmd_start)

    s = sub.add_parser("say", help="send a student turn, print Lena's reply")
    s.add_argument("text")
    s.set_defaults(fn=cmd_say)

    s = sub.add_parser("transcript", help="print the conversation so far")
    s.set_defaults(fn=cmd_transcript)

    s = sub.add_parser("stop", help="close the session (debrief fires on disconnect)")
    s.set_defaults(fn=cmd_stop)

    s = sub.add_parser("hold", help=argparse.SUPPRESS)
    s.add_argument("--url", required=True)
    s.add_argument("--ready-file", required=True)
    s.set_defaults(fn=cmd_hold)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
