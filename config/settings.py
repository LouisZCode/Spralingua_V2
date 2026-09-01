"""
env api keay load, variables, and defaults

"""

import os
from dotenv import load_dotenv

load_dotenv()

#Deepgram
deepgram_api_key=os.getenv("DEEPGRAM_API_KEY")

#Minimax
minimax_api_key=os.getenv("MINIMAX_API_KEY")
minimax_group_id=os.getenv("MINIMAX_GROUP_ID")

#OpenRouter
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

#Cerebras (judge primary leg — direct, no OpenRouter hop; see agents/openrouter_llm.py)
cerebras_api_key = os.getenv("CEREBRAS_API_KEY")

#Langfuse
langfuse_public_key  = os.getenv("LANGFUSE_PUBLIC_KEY")
langfuse_secret_key  = os.getenv("LANGFUSE_SECRET_KEY")
langfuse_base_url    = os.getenv("LANGFUSE_BASE_URL")
langfuse_environment = os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "dev")

#Azure Speech (Pronunciation Assessment, PRON-001)
azure_speech_key    = os.getenv("AZURE_SPEECH_KEY")
azure_speech_region = os.getenv("AZURE_SPEECH_REGION", "eastus")

#Postgres (DATA-001). No default — absence raises at startup (fail-loud).
# Form: postgresql+asyncpg://user:password@host:port/dbname
# Managed hosts (Railway/Render/Neon/Supabase) inject a plain `postgresql://`
# (or legacy `postgres://`) URL, but our async engine needs the asyncpg driver.
# Normalize the scheme here so DATABASE_URL is portable; alembic/env.py swaps
# +asyncpg -> +psycopg2 for its sync migrations.
_raw_db_url = os.getenv("DATABASE_URL")
if _raw_db_url:
    for _prefix in ("postgresql://", "postgres://"):
        if _raw_db_url.startswith(_prefix):
            _raw_db_url = "postgresql+asyncpg://" + _raw_db_url[len(_prefix):]
            break
database_url = _raw_db_url

# --- Test isolation guard rail (TEST-001) ---
# When set, every write helper in database/repository.py that takes a
# user_id refuses to run unless that id starts with "test-" (see
# database.repository._assert_test_user and CLAUDE.md's sim-harness
# section). Off by default: prod and any ordinary dev run are unaffected.
# Opt in for a sim/test session so a stray write to a real account (e.g.
# 0001) crashes loudly instead of silently mutating real learning data —
# that's how the TEST-001 incident happened.
test_guard_enabled = os.getenv("SPRALINGUA_TEST_GUARD", "0") == "1"

# --- Front-page demo agent hardening (SEC-001) ---
# The "/ws/demo/{id}" + "/say" surface is world-facing and unauthenticated, so
# it gets its own guardrails (see security.py). Everything is env-overridable so
# prod can tighten without code edits. Counters are in-memory/per-process —
# behind multiple workers or hosts, enforce at a gateway/WAF or move to Redis.

# Origins allowed to open the demo WebSocket and call /say. WebSockets aren't
# subject to CORS, so we check the Origin header ourselves. Comma-separated.
allowed_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

# Wall-clock cap (seconds) on a single demo session — bounds Deepgram stream
# time and total cost even if the visitor never trips max_exchanges.
demo_session_timeout_s = int(os.getenv("DEMO_SESSION_TIMEOUT_S", "180"))

# Wall-clock cap (seconds) on a single authenticated session (MEMORY-002,
# 2026-09-01). Authenticated routes ran with no watchdog at all: a learner who
# closes the tab without ending the session keeps a half-open socket alive
# (BUG-009's keepalive frames actively feed Railway's edge), and the pipeline's
# AudioBufferProcessor keeps accumulating session audio (~3.8 MB/min with turn
# recording) for as long as it lives. 2h is far beyond any real lesson — the
# production p90 is ~17 min (Langfuse, Aug 30 – Sep 1) — while still forgiving
# a paused "I'll be right back" stretch.
learned_session_timeout_s = int(os.getenv("LEARNED_SESSION_TIMEOUT_S", "7200"))

# Concurrency + rate caps for the public demo.
demo_max_concurrent        = int(os.getenv("DEMO_MAX_CONCURRENT", "25"))        # global live demo pipelines
demo_per_ip_concurrent     = int(os.getenv("DEMO_PER_IP_CONCURRENT", "2"))
demo_per_ip_new_per_window = int(os.getenv("DEMO_PER_IP_NEW_PER_WINDOW", "20"))
demo_per_ip_window_s       = int(os.getenv("DEMO_PER_IP_WINDOW_S", "600"))      # 10 min
demo_global_new_per_min    = int(os.getenv("DEMO_GLOBAL_NEW_PER_MIN", "60"))    # bounds IP-rotation abuse

# /say typed-turn guards (apply to every session; the demo relies on /say).
say_max_chars         = int(os.getenv("SAY_MAX_CHARS", "500"))
say_per_ip_interval_s = float(os.getenv("SAY_PER_IP_INTERVAL_S", "2"))

# IPs exempt from per-IP / global *rate* limits (local dev). The Origin check
# and the global concurrency cap still apply to them.
rate_limit_exempt_ips = {
    ip.strip()
    for ip in os.getenv("RATE_LIMIT_EXEMPT_IPS", "127.0.0.1,::1,localhost").split(",")
    if ip.strip()
}

# --- Deployment ---
# "dev" (default) preserves local ergonomics — e.g. auth/tokens.py falls back to
# an ephemeral JWT secret. Set APP_ENV=production (anything != "dev") in any
# deployed environment so missing-secret misconfig fails loud instead.
app_env = os.getenv("APP_ENV", "dev")

# --- Authentication (AUTH-001) ---
# Google sign-in (P-3): the frontend obtains a Google ID token and POSTs it to
# /auth/google; the backend verifies it against this client id, then issues its
# own session JWT. `google_client_id` is required only to actually verify a
# Google token — the server still boots without it (the /auth/google route 503s
# until it's set), so local non-auth work isn't blocked.
google_client_id = os.getenv("GOOGLE_CLIENT_ID")

# HS256 secret for the backend-issued session JWT. If unset, `auth/tokens.py`
# mints an ephemeral per-process secret (dev only) and logs a warning — those
# tokens don't survive a restart and differ per worker, so set JWT_SECRET in any
# shared/deployed environment.
jwt_secret = os.getenv("JWT_SECRET")
jwt_expiry_days = int(os.getenv("JWT_EXPIRY_DAYS", "7"))

# --- Stripe billing (PAY-001) ---
# Absent -> every /payments route 503s "billing not configured" (fail-soft,
# same contract as azure_speech_key / google_client_id): the app must boot
# fine with no Stripe env set at all.
stripe_secret_key = os.getenv("STRIPE_SECRET_KEY")
stripe_webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
stripe_basic_price_id = os.getenv("STRIPE_BASIC_PRICE_ID")
stripe_premium_price_id = os.getenv("STRIPE_PREMIUM_PRICE_ID")
# Off by default until Stripe Tax is turned on in the Dashboard — passing
# automatic_tax=enabled on a Checkout Session without Dashboard tax setup
# errors the call, so this stays "0" until that configuration is done.
stripe_automatic_tax = os.getenv("STRIPE_AUTOMATIC_TAX", "0") == "1"
# Where Checkout/portal success, cancel, and return URLs point back to.
frontend_base_url = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000")

# PAY-002 top-up (one-time 500 coins for €2). Absent → POST /payments/topup/checkout 503s
# "billing not configured", same fail-soft contract as the PAY-001 price ids.
stripe_topup_price_id = os.getenv("STRIPE_TOPUP_PRICE_ID")

# --- Interview audio bucket (INTV-003 slice 2) ---
# Railway Bucket "audio" (S3-compatible, Tigris-backed) credentials for
# presigning GET URLs to interview chunk mp3s (interview/bucket.py). All
# optional: absent/incomplete -> GET /interview/audio/{chunk_id} 503s with a
# clear detail (interview/bucket.py logs one warning) instead of crashing;
# nothing else at startup depends on these being set.
bucket_endpoint = os.getenv("BUCKET_ENDPOINT")
bucket_region = os.getenv("BUCKET_REGION")
bucket_name = os.getenv("BUCKET_NAME")
bucket_access_key_id = os.getenv("BUCKET_ACCESS_KEY_ID")
bucket_secret_access_key = os.getenv("BUCKET_SECRET_ACCESS_KEY")