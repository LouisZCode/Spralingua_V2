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