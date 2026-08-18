"""Presigned-URL audio serving for the interview exercise (INTV-003 slice 2),
ported from ``interview_local/app.py``'s ``INTERVIEW_AUDIO_SOURCE=bucket``
block (``_get_bucket_client`` / ``_presigned_audio_redirect``).

Two differences from the workbench:

- Credentials come from ``config.settings``' ``BUCKET_*`` vars (added
  alongside the app's other settings, following ``config/settings.py``'s
  existing ``os.getenv(...)`` convention) instead of a separate
  ``.env.bucket`` file loaded lazily via ``python-dotenv`` — this package
  has no local-only/main-repo split to preserve.
- Production audio lives ONLY in the bucket — there is no local mp3 copy to
  fall back to like the workbench's disk-serving default — so a presign
  failure here surfaces as a 503 to the caller (``interview/routes.py``)
  instead of silently serving from disk. The graceful-degradation contract
  is otherwise identical: any failure (missing settings, a bad client, a
  presign error) logs exactly one warning and returns ``None``; this module
  never raises.
"""

from typing import Optional

import boto3
from loguru import logger

from config.settings import (
    bucket_access_key_id,
    bucket_endpoint,
    bucket_name,
    bucket_region,
    bucket_secret_access_key,
)

# Same expiry as the workbench (interview_local/app.py::_BUCKET_PRESIGN_EXPIRY_S).
PRESIGN_EXPIRY_S = 15 * 60

# Lazily built (client, bucket_name) tuple; `False` sentinel = "tried, unavailable".
_bucket_client_cache = None


def _get_bucket_client():
    """Build and cache the boto3 S3 client for the Railway Bucket. Returns
    ``None`` if any ``BUCKET_*`` setting is missing or client construction
    fails — this must never raise. Logs one warning on the FIRST failed
    attempt only; later calls just return the cached ``None`` silently
    (same as the workbench)."""
    global _bucket_client_cache
    if _bucket_client_cache is not None:
        return _bucket_client_cache or None

    required = {
        "BUCKET_ENDPOINT": bucket_endpoint,
        "BUCKET_REGION": bucket_region,
        "BUCKET_NAME": bucket_name,
        "BUCKET_ACCESS_KEY_ID": bucket_access_key_id,
        "BUCKET_SECRET_ACCESS_KEY": bucket_secret_access_key,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.warning(
            f"interview audio bucket unavailable — missing settings: {', '.join(missing)}"
        )
        _bucket_client_cache = False
        return None

    try:
        client = boto3.client(
            "s3",
            endpoint_url=bucket_endpoint,
            region_name=bucket_region,
            aws_access_key_id=bucket_access_key_id,
            aws_secret_access_key=bucket_secret_access_key,
        )
        _bucket_client_cache = (client, bucket_name)
    except Exception as exc:  # noqa: BLE001 -- any failure here must degrade to a 503, never crash
        logger.warning(f"interview audio bucket client construction failed: {exc}")
        _bucket_client_cache = False
        return None
    return _bucket_client_cache


def presigned_audio_url(storage_key: str) -> Optional[str]:
    """Best-effort presigned GET URL for ``storage_key`` (an
    ``AudioChunk.storage_key``, e.g. ``"interviews/<dir>/chunk_001.mp3"``),
    or ``None`` on any failure so the caller can 503. Logs one warning per
    presign failure; never raises."""
    bucket_client = _get_bucket_client()
    if bucket_client is None:
        return None
    client, name = bucket_client
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": name, "Key": storage_key},
            ExpiresIn=PRESIGN_EXPIRY_S,
        )
    except Exception as exc:  # noqa: BLE001 -- presign failure must 503, never crash
        logger.warning(f"presign failed for {storage_key}: {exc}")
        return None
