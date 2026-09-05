"""Learner voice-recording archive (REL-001).

Every graded spoken-drill attempt and every voice-session MP3 lands in one
S3-compatible bucket (Railway Bucket, EU-West — a sibling of the existing
"audio" interview bucket, created separately in the Railway dashboard),
under `recordings/store.py`'s `voice/{user_id}/{YYYY}/{MM}/{DD}/{surface}/`
layout, with one `voice_recordings` row per clip (`database/orm.py`)
linking WHO / WHEN / WHAT exercise to the object.

Deliberately no HTTP router here — this package is a write-only backend
service the six drill audio routes and the voice-session disconnect path
call into (`recordings/service.py::schedule_recording` /
`save_recording_now`), never a router `main.py` mounts. Keep this
`__init__.py` free of heavy imports (no `agents.*`, no FastAPI app plumbing)
so importing `recordings.store` in isolation — e.g. from a future
standalone script, the way `scripts/pg_dump_to_bucket.py` avoids importing
`interview.bucket` — never risks pulling in the full router stack.
"""
