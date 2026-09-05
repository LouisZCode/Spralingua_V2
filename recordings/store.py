"""S3-compatible object storage for the voice-recording bucket (REL-001).

Mirrors ``interview/bucket.py``'s ``_get_bucket_client`` shape exactly (same
five-line ``boto3.client("s3", ...)`` construction, same "cache a ``False``
sentinel after the first failed attempt so only one warning is ever logged"
discipline) but reads the ``VOICE_BUCKET_*`` settings instead of
``BUCKET_*``, and is duplicated rather than imported — ``import
interview.bucket`` runs ``interview/__init__.py`` -> ``interview/routes.py``,
which pulls in the full FastAPI router stack (``agents.*`` included) at
import time; the same reason ``scripts/pg_dump_to_bucket.py`` duplicates
this construction instead of importing it. This module must stay importable
on its own with nothing more than ``boto3`` + ``config.settings``.

Every function here is best-effort: a missing setting, a bad client, or an
upload failure logs and returns a falsy/``None`` value, never raises — a
recording is a nice-to-have archive, not something that may ever break the
attempt or session it rides on.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import boto3
from loguru import logger

from config.settings import (
    voice_bucket_access_key_id,
    voice_bucket_endpoint,
    voice_bucket_name,
    voice_bucket_region,
    voice_bucket_secret_access_key,
)

# Lazily built (client, bucket_name) tuple; `False` sentinel = "tried,
# unavailable" — same caching contract as interview/bucket.py, so a missing
# config warns exactly once per process, not once per recording.
_bucket_client_cache = None


def _get_bucket_client():
    """Build and cache the boto3 S3 client for the voice bucket. Returns
    ``None`` if any ``VOICE_BUCKET_*`` setting is missing or client
    construction fails — this must never raise. Logs one warning on the
    FIRST failed attempt only; later calls just return the cached ``None``
    silently."""
    global _bucket_client_cache
    if _bucket_client_cache is not None:
        return _bucket_client_cache or None

    required = {
        "VOICE_BUCKET_ENDPOINT": voice_bucket_endpoint,
        "VOICE_BUCKET_REGION": voice_bucket_region,
        "VOICE_BUCKET_NAME": voice_bucket_name,
        "VOICE_BUCKET_ACCESS_KEY_ID": voice_bucket_access_key_id,
        "VOICE_BUCKET_SECRET_ACCESS_KEY": voice_bucket_secret_access_key,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.warning(
            f"voice recording bucket unavailable — missing settings: {', '.join(missing)}"
        )
        _bucket_client_cache = False
        return None

    try:
        client = boto3.client(
            "s3",
            endpoint_url=voice_bucket_endpoint,
            region_name=voice_bucket_region,
            aws_access_key_id=voice_bucket_access_key_id,
            aws_secret_access_key=voice_bucket_secret_access_key,
        )
        _bucket_client_cache = (client, voice_bucket_name)
    except Exception as exc:  # noqa: BLE001 -- any failure here must degrade to a no-op, never crash
        logger.warning(f"voice recording bucket client construction failed: {exc}")
        _bucket_client_cache = False
        return None
    return _bucket_client_cache


def is_configured() -> bool:
    """True once all five ``VOICE_BUCKET_*`` settings are present AND the
    client actually constructed. Cheap after the first call (cached)."""
    return _get_bucket_client() is not None


# MediaRecorder's own candidate list (frontend/src/components/shared/
# recorder.ts::MIME_CANDIDATES) plus the formats the batch-STT surfaces
# (satz/examiner.py::transcribe_attempt) already accept — a best-effort
# extension guess, never load-bearing (the DB row keeps the real
# content_type verbatim regardless of what this maps to).
_EXT_BY_SUBTYPE = {
    "webm": "webm",
    "mp4": "m4a",
    "wav": "wav",
    "wave": "wav",
    "x-wav": "wav",
    "ogg": "ogg",
    "mpeg": "mp3",
    "mp3": "mp3",
}


def ext_for_content_type(content_type: Optional[str]) -> str:
    """Best-effort file extension from an ``UploadFile.content_type`` (e.g.
    ``"audio/webm;codecs=opus"`` -> ``"webm"``). Falls back to the raw
    subtype, or ``"bin"`` when there's nothing to go on — this only shapes
    the object key, so a wrong guess costs nothing beyond a slightly odd
    key suffix."""
    if not content_type:
        return "bin"
    base = content_type.split(";", 1)[0].strip().lower()
    subtype = base.split("/", 1)[-1]
    return _EXT_BY_SUBTYPE.get(subtype, subtype or "bin")


def build_key(
    user_id: str, surface: str, recording_id: str, ext: str, now: Optional[datetime] = None
) -> str:
    """``voice/{user_id}/{YYYY}/{MM}/{DD}/{surface}/{recording_id}.{ext}`` —
    one folder per learner per day per surface ("well organized" per Luis's
    ask). ``now`` defaults to the current UTC time; callers pass it
    explicitly only in tests."""
    now = now or datetime.now(timezone.utc)
    return f"voice/{user_id}/{now:%Y}/{now:%m}/{now:%d}/{surface}/{recording_id}.{ext}"


def _put_object_sync(client, bucket: str, key: str, data: bytes, content_type: str) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


async def upload_bytes(key: str, data: bytes, content_type: str) -> bool:
    """Best-effort upload; ``True`` on success, ``False`` on any failure
    (including "not configured"). Runs the blocking boto3 call in a worker
    thread (``asyncio.to_thread``) so the event loop is never blocked —
    every call site here already runs off the request/session's critical
    path, but the upload itself must not stall whatever thread it's on
    either."""
    bucket_client = _get_bucket_client()
    if bucket_client is None:
        return False
    client, bucket = bucket_client
    try:
        await asyncio.to_thread(_put_object_sync, client, bucket, key, data, content_type)
        return True
    except Exception as exc:  # noqa: BLE001 -- an upload failure must never raise into the caller
        logger.warning(f"voice recording upload failed for {key}: {exc}")
        return False


def _delete_object_sync(client, bucket: str, key: str) -> None:
    client.delete_object(Bucket=bucket, Key=key)


async def delete_object(key: str) -> bool:
    """Best-effort delete. Not called from the app itself anywhere — this
    exists for test/cleanup scripts (e.g. wiping a `voice/test-*/...`
    fixture prefix) that want the same client-construction discipline as
    everything else in this module rather than rolling their own boto3
    call."""
    bucket_client = _get_bucket_client()
    if bucket_client is None:
        return False
    client, bucket = bucket_client
    try:
        await asyncio.to_thread(_delete_object_sync, client, bucket, key)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"voice recording delete failed for {key}: {exc}")
        return False
