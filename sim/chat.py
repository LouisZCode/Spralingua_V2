"""Tandem conversation simulator (TAND-002 calibration harness).

Drives a REAL local pipeline session over text only: a background `hold`
process keeps the WebSocket open (audio frames are received and discarded),
and each student turn goes in through `POST /say/{user_id}` — which the
endpoint guarantees is identical to a spoken turn (TTS, Langfuse, goodbye
detection, exchange count all fire). Lena's replies are read from the live
session transcript that `logs/session_logger.py` writes.

STT is skipped entirely (no audio in). TTS still synthesizes in the
background; the audio is discarded by the holder.

Usage (from repo root, backend running on :8765, Postgres up):
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
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUN_DIR = Path(__file__).resolve().parent / ".run"
STATE = RUN_DIR / "state.json"
LOG_ROOT = REPO / "logs" / "conversations"
BASE = os.environ.get("SIM_BASE", "http://127.0.0.1:8765")
WS_BASE = BASE.replace("http", "ws", 1)

# One message block in the transcript: "Label: text" (text may wrap onto
# following lines until a blank line or the --- separator).
_MSG_RE = re.compile(r"^([A-Za-zÄÖÜäöüß_ .-]{1,40}): (.*)$")


def _load_state() -> dict:
    if not STATE.exists():
        sys.exit("no active sim session — run `start` first")
    return json.loads(STATE.read_text())


def _save_state(state: dict) -> None:
    RUN_DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _parse_messages(md_path: Path) -> list[tuple[str, str]]:
    """[(speaker, text)] from the '## Conversation' section, in order."""
    if not md_path.exists():
        return []
    text = md_path.read_text(encoding="utf-8")
    _, _, conv = text.partition("## Conversation")
    messages: list[tuple[str, str]] = []
    current: list[str] | None = None
    speaker = ""
    for line in conv.splitlines():
        m = _MSG_RE.match(line)
        if m:
            if current is not None:
                messages.append((speaker, " ".join(current).strip()))
            speaker, first = m.group(1), m.group(2)
            current = [first]
        elif current is not None:
            if line.strip() in ("", "---"):
                messages.append((speaker, " ".join(current).strip()))
                current = None
            else:
                current.append(line.strip())
    if current is not None:
        messages.append((speaker, " ".join(current).strip()))
    return messages


def _bot_messages(md_path: Path) -> list[str]:
    return [t for s, t in _parse_messages(md_path) if s != "User"]


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
        {"lesson": "tandem", "topic": args.topic, "voice": args.voice, "token": token}
    )
    url = f"{WS_BASE}/ws/{args.user}?{qs}"

    before = set(LOG_ROOT.glob("*/session_*.md"))
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

    # Wait for the WS to be accepted, then for the session logger's new files.
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

    md_path: Path | None = None
    deadline = time.time() + 15
    while time.time() < deadline and md_path is None:
        new = set(LOG_ROOT.glob("*/session_*.md")) - before
        if new:
            md_path = sorted(new)[-1]
        else:
            time.sleep(0.3)
    if md_path is None:
        proc.terminate()
        sys.exit("no new session transcript appeared — check backend logs")

    _save_state(
        {
            "pid": proc.pid,
            "md": str(md_path),
            "bot_seen": 0,
            "user": args.user,
            "token": token,
            "topic": args.topic,
        }
    )
    print(f"session started (transcript: {md_path.name}, topic: {args.topic!r})")
    print("you speak first — send a greeting with: say \"Hallo Lena!\"")


def cmd_say(args: argparse.Namespace) -> None:
    state = _load_state()
    md_path = Path(state["md"])
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

    # Wait for a NEW bot message in the transcript.
    deadline = time.time() + 90
    while time.time() < deadline:
        bots = _bot_messages(md_path)
        if len(bots) > state["bot_seen"]:
            time.sleep(1.2)  # settle: let the full block flush to disk
            bots = _bot_messages(md_path)
            state["bot_seen"] = len(bots)
            _save_state(state)
            print(bots[-1])
            return
        if not _alive(state["pid"]):
            # Pipeline may have closed AFTER answering (goodbye detection).
            time.sleep(1.5)
            bots = _bot_messages(md_path)
            if len(bots) > state["bot_seen"]:
                state["bot_seen"] = len(bots)
                _save_state(state)
                print(bots[-1])
                print("SESSION_ENDED (pipeline closed after this reply)")
                return
            print("SESSION_ENDED (connection closed, no reply)")
            return
        time.sleep(0.5)
    sys.exit("timed out waiting for Lena's reply (90s)")


def cmd_transcript(_args: argparse.Namespace) -> None:
    state = _load_state()
    for speaker, text in _parse_messages(Path(state["md"])):
        who = "STUDENT" if speaker == "User" else "LENA"
        print(f"{who}: {text}")


def cmd_stop(_args: argparse.Namespace) -> None:
    state = _load_state()
    if _alive(state["pid"]):
        os.kill(state["pid"], signal.SIGTERM)
        time.sleep(1.0)
    print(f"stopped (transcript: {state['md']})")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="open a tandem session (holder in background)")
    s.add_argument("--topic", required=True)
    s.add_argument("--user", default="0001")
    s.add_argument("--voice", default="German_Female")
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
