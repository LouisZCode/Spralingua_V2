"""
One-off: prune redundant MiniMax cloned voices.

Dry-run by default. Pass --execute to actually delete.
Deletion is IRREVERSIBLE (voice_id cannot be reused) — see
https://platform.minimax.io/docs/api-reference/voice-management-delete

Reads MINIMAX_API_KEY from .env (not committed).
"""

import json
import sys
import urllib.request
from pathlib import Path

ENDPOINT = "https://api.minimax.io/v1/delete_voice"

# --- Decided with the user (see conversation). KEEP is a safety guard only. ---
KEEP = {
    "moss_audio_744c4375-eb2b-11f0-b8d7-fa843b4be43a",  # luis_clone (best Luis)
    "moss_audio_4872e74b-124f-11f1-841b-1e2fac512910",  # German_Female (won A/B vs v2)
}

DELETE = [
    "moss_audio_1068fa1d-124f-11f1-83e4-92355c742862",   # german_female_v2 — lost A/B, user rejected
]


def load_api_key() -> str:
    env = Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("MINIMAX_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("MINIMAX_API_KEY not found in .env")


def delete_voice(api_key: str, voice_id: str) -> dict:
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps({"voice_type": "voice_cloning", "voice_id": voice_id}).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> None:
    execute = "--execute" in sys.argv

    # Hard guard: never let a keep-ID slip into the delete list.
    collision = KEEP.intersection(DELETE)
    if collision:
        raise SystemExit(f"ABORT: keep-IDs present in delete list: {collision}")

    print(f"KEEP ({len(KEEP)}):")
    for v in sorted(KEEP):
        print(f"  - {v}")
    print(f"\nDELETE ({len(DELETE)}):")
    for v in DELETE:
        print(f"  - {v}")

    if not execute:
        print("\n[DRY RUN] Nothing deleted. Re-run with --execute to delete.")
        return

    api_key = load_api_key()
    print("\n[EXECUTE] Deleting...\n")
    for v in DELETE:
        try:
            r = delete_voice(api_key, v)
            code = r.get("base_resp", {}).get("status_code")
            msg = r.get("base_resp", {}).get("status_msg")
            ok = "OK" if code == 0 else f"FAIL({code})"
            print(f"  [{ok}] {v}  ->  {msg}")
        except Exception as e:  # noqa: BLE001
            print(f"  [ERROR] {v}  ->  {e}")


if __name__ == "__main__":
    main()
