"""REL-002: weekly offsite Postgres backup (pg_dump -> Railway Bucket).

Railway cron setup (developer's step, done in the Railway dashboard):
  - Image: this repo's own backend image (same Dockerfile) -- `pg_dump` is
    on PATH there (see the Dockerfile's `postgresql-client` runtime package).
  - Start command: `python scripts/pg_dump_to_bucket.py`
  - Suggested schedule: `0 3 * * 0` (weekly, Sunday 03:00 UTC).
  - Env vars: `DATABASE_URL`, `BUCKET_ENDPOINT`, `BUCKET_REGION`,
    `BUCKET_NAME`, `BUCKET_ACCESS_KEY_ID`, `BUCKET_SECRET_ACCESS_KEY`, and
    optionally `BACKUP_RETENTION_DAYS` (default 35).
  - Restore drill: `pg_restore --list <file>` to inspect a dump, then
    `pg_restore -d <throwaway-db> <file>` to actually restore it.

Standalone by design, unlike the rest of this repo's DB tooling: no FastAPI
import, no SQLAlchemy engine init (`database/connection.py::init_engine` is
never called) -- Postgres is only ever touched via the `pg_dump` binary as a
subprocess, so this can run in a bare cron container with no asyncpg pool to
manage or tear down.

Doesn't import `interview.bucket` for the S3 client, even though the
construction is identical (same five `BUCKET_*` settings, same
`boto3.client("s3", ...)` call). `import interview.bucket` runs
`interview/__init__.py` -> `interview/routes.py`, which pulls in the full
FastAPI router stack plus `agents.*` -- including
`agents/conversation_agent.py`'s module-level `ChatOpenAI` client, which
raises at import time unless `OPENROUTER_API_KEY` is set (see
`scripts/ci_smoke.py`'s docstring for the same gotcha). Wrong shape for a
cron script whose only dependency should be Postgres + the bucket, so the
same five-line client construction is duplicated here instead of imported.
`interview/bucket.py` itself is untouched.

Reads `DATABASE_URL` from `.env` via `config.settings` and never prints it
(or any other secret), per this repo's "never print .env values" rule.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Run as `uv run python scripts/pg_dump_to_bucket.py …` from anywhere — make
# sure the repo root (not scripts/) is on sys.path so `config` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402

from config.settings import (  # noqa: E402
    backup_retention_days,
    bucket_access_key_id,
    bucket_endpoint,
    bucket_name,
    bucket_region,
    bucket_secret_access_key,
    database_url,
)

BACKUP_PREFIX = "backups/postgres/"
DUMP_BASENAME = "spralingua.dump"


def _fail(message: str) -> None:
    """Print a clear reason to stderr and exit non-zero. Every failure path
    in this script goes through here so the cron job's logs always say why,
    never just a traceback."""
    print(f"pg_dump_to_bucket: {message}", file=sys.stderr)
    sys.exit(1)


def libpq_url(sqlalchemy_url: str) -> str:
    """SQLAlchemy asyncpg DSN -> plain libpq URL `pg_dump` understands.

    Same driver-stripping move as `alembic/env.py`, but to a bare
    `postgresql://` instead of swapping to `+psycopg2` -- `pg_dump` isn't a
    SQLAlchemy dialect, it just wants a libpq connection string.
    """
    if not sqlalchemy_url:
        _fail(
            "DATABASE_URL is not set. Add it to the environment "
            "(form: postgresql+asyncpg://user:password@host:port/dbname)."
        )
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def require_bucket_settings() -> None:
    """Fail loud if any BUCKET_* setting is missing. Unlike
    `interview/bucket.py::_get_bucket_client` (which degrades to a 503 for a
    web request), a cron job with no bucket creds is just misconfigured --
    there is no caller to hand a graceful failure to."""
    required = {
        "BUCKET_ENDPOINT": bucket_endpoint,
        "BUCKET_REGION": bucket_region,
        "BUCKET_NAME": bucket_name,
        "BUCKET_ACCESS_KEY_ID": bucket_access_key_id,
        "BUCKET_SECRET_ACCESS_KEY": bucket_secret_access_key,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        _fail(f"missing required bucket setting(s): {', '.join(missing)}")


def build_bucket_client():
    """Same construction as `interview/bucket.py::_get_bucket_client()`
    (region + endpoint + static credentials), minus that function's
    fail-soft/caching behavior -- see the module docstring for why this
    isn't imported directly."""
    require_bucket_settings()
    try:
        return boto3.client(
            "s3",
            endpoint_url=bucket_endpoint,
            region_name=bucket_region,
            aws_access_key_id=bucket_access_key_id,
            aws_secret_access_key=bucket_secret_access_key,
        )
    except Exception as exc:  # noqa: BLE001 -- any construction failure must fail loud here
        _fail(f"bucket client construction failed: {exc}")


def run_pg_dump(pg_url: str, dest_path: Path) -> None:
    """Custom-format dump (already compressed, no gzip needed) to
    `dest_path`. Fails loud on a missing binary or a non-zero exit."""
    if shutil.which("pg_dump") is None:
        _fail(
            "pg_dump not found on PATH. Install postgresql-client "
            "(see the Dockerfile's runtime stage)."
        )
    result = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(dest_path),
            pg_url,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _fail(
            f"pg_dump exited {result.returncode}: "
            f"{result.stderr.strip() or '(no stderr output)'}"
        )


def backup_key(now: datetime) -> str:
    """`backups/postgres/<YYYY-MM-DD>T<HHMM>Z-spralingua.dump`, UTC."""
    return f"{BACKUP_PREFIX}{now.strftime('%Y-%m-%dT%H%M')}Z-{DUMP_BASENAME}"


def list_backup_objects(client, bucket: str) -> list[dict]:
    """All S3 objects under `BACKUP_PREFIX`, paginated via
    `list_objects_v2`'s own truncation fields (no `get_paginator` -- keeps
    this callable against a plain fake client in tests, not just a full
    boto3 client)."""
    objects: list[dict] = []
    kwargs = {"Bucket": bucket, "Prefix": BACKUP_PREFIX}
    while True:
        resp = client.list_objects_v2(**kwargs)
        objects.extend(resp.get("Contents") or [])
        token = resp.get("NextContinuationToken")
        if resp.get("IsTruncated") and token:
            kwargs["ContinuationToken"] = token
        else:
            break
    return objects


def prune_stale(
    client, bucket: str, retention_days: int, now: datetime, dry_run: bool
) -> list[str]:
    """Delete objects under `BACKUP_PREFIX` older than `retention_days`.
    Never touches a key outside that prefix -- both the list call's
    `Prefix` and this function's own filter enforce it."""
    cutoff = now - timedelta(days=retention_days)
    stale_keys = [
        obj["Key"]
        for obj in list_backup_objects(client, bucket)
        if obj["Key"].startswith(BACKUP_PREFIX) and obj["LastModified"] < cutoff
    ]
    if not stale_keys:
        return []
    if dry_run:
        for key in stale_keys:
            print(f"[dry-run] would delete s3://{bucket}/{key}")
        return stale_keys
    # delete_objects takes at most 1000 keys per call.
    for i in range(0, len(stale_keys), 1000):
        batch = stale_keys[i : i + 1000]
        client.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": k} for k in batch]}
        )
    return stale_keys


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dump Postgres (pg_dump --format=custom) and ship it to the "
            "backups/postgres/ prefix of the Railway Bucket, pruning dumps "
            "older than BACKUP_RETENTION_DAYS."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dump locally and list what would be uploaded/deleted, but make no bucket writes.",
    )
    parser.add_argument(
        "--keep-local",
        type=Path,
        default=None,
        metavar="PATH",
        help="Also copy the dump file to this local path before it's cleaned up.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    pg_url = libpq_url(database_url)
    if shutil.which("pg_dump") is None:
        _fail(
            "pg_dump not found on PATH. Install postgresql-client "
            "(see the Dockerfile's runtime stage)."
        )
    require_bucket_settings()

    now = datetime.now(timezone.utc)
    key = backup_key(now)
    client = build_bucket_client()

    with tempfile.TemporaryDirectory(prefix="spralingua-pgdump-") as tmpdir:
        dump_path = Path(tmpdir) / DUMP_BASENAME
        run_pg_dump(pg_url, dump_path)
        size_bytes = dump_path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)

        if args.keep_local:
            shutil.copy(dump_path, args.keep_local)
            print(f"pg_dump_to_bucket: kept local copy at {args.keep_local}")

        if args.dry_run:
            print(
                f"[dry-run] would upload {dump_path} -> "
                f"s3://{bucket_name}/{key} ({size_mb:.2f} MB)"
            )
        else:
            client.upload_file(str(dump_path), bucket_name, key)

        # A prune failure after a successful upload must not read as a failed
        # backup: say the dump landed, name the prune error, still exit
        # non-zero so the cron alerts.
        try:
            pruned = prune_stale(
                client, bucket_name, backup_retention_days, now, args.dry_run
            )
        except Exception as exc:  # noqa: BLE001 — report, don't traceback
            print(
                f"pg_dump_to_bucket: OK key={key} size_mb={size_mb:.2f} "
                f"(upload succeeded; prune FAILED: {type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            return 1

    print(
        f"pg_dump_to_bucket: OK key={key} size_mb={size_mb:.2f} "
        f"pruned={len(pruned)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
