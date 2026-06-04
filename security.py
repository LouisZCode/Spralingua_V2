"""
In-memory guardrails for the public, unauthenticated demo agent (SEC-001).

The "/ws/demo/{id}" + "/say" surface is world-facing: anyone can open it and
drive our STT -> LLM -> TTS stack, which costs money per second of audio. These
helpers add defense-in-depth without new infra:

  * an Origin allowlist (WebSocket handshakes aren't subject to browser CORS, so
    we check the Origin header ourselves);
  * a global concurrency cap (hard ceiling on simultaneous live pipelines/cost);
  * a per-IP concurrency cap;
  * sliding-window new-session rate limits, per-IP and global (the global one
    blunts IP-rotation abuse that would slip past the per-IP window);
  * a per-IP minimum interval between typed /say turns.

State is in-memory and per-process. Behind multiple workers/hosts these counters
are per-worker only -- enforce the real limits at a gateway/WAF or move state to
Redis. The functions never await, so on uvicorn's single asyncio loop each call
runs to completion without interleaving; that's why plain dicts are safe here.
"""

import time
from collections import deque

from config.settings import (
    allowed_origins,
    demo_max_concurrent,
    demo_per_ip_concurrent,
    demo_per_ip_new_per_window,
    demo_per_ip_window_s,
    demo_global_new_per_min,
    say_per_ip_interval_s,
    rate_limit_exempt_ips,
)

# --- in-memory state (per-process) ---
_demo_concurrent_total = 0
_demo_concurrent_by_ip: dict[str, int] = {}
_demo_new_by_ip: dict[str, deque] = {}   # IP -> timestamps of recent new sessions
_demo_new_global: deque = deque()        # timestamps of all recent new sessions
_say_last_by_ip: dict[str, float] = {}   # IP -> monotonic time of last accepted /say

# Opportunistic memory reclaim so a long-running process under IP-rotation abuse
# doesn't accumulate dead per-IP entries forever.
_last_prune = 0.0
_PRUNE_INTERVAL_S = 60.0


def _is_exempt(ip: str) -> bool:
    return ip in rate_limit_exempt_ips


def _evict(dq: deque, now: float, window_s: float) -> None:
    """Drop timestamps older than `window_s` from the left of `dq`."""
    cutoff = now - window_s
    while dq and dq[0] < cutoff:
        dq.popleft()


def _maybe_prune(now: float) -> None:
    """Sweep stale per-IP entries at most once per minute (bounds memory)."""
    global _last_prune
    if now - _last_prune < _PRUNE_INTERVAL_S:
        return
    _last_prune = now
    for ip in list(_demo_new_by_ip.keys()):
        dq = _demo_new_by_ip[ip]
        _evict(dq, now, demo_per_ip_window_s)
        if not dq:
            del _demo_new_by_ip[ip]
    stale_before = now - say_per_ip_interval_s
    for ip in list(_say_last_by_ip.keys()):
        if _say_last_by_ip[ip] < stale_before:
            del _say_last_by_ip[ip]


def client_ip(conn) -> str:
    """Best-effort client IP for a Starlette Request or WebSocket.

    Honors the first hop of X-Forwarded-For when present (prod is expected to sit
    behind a proxy/load balancer); falls back to the socket peer. If you deploy
    behind a proxy that lets clients spoof XFF, strip/override it at the edge.
    """
    xff = conn.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    client = conn.client
    return client.host if client else "unknown"


def origin_allowed(origin: str | None) -> bool:
    """True iff the request's Origin header is in the allowlist.

    A missing Origin is rejected: real browsers always send one for WS handshakes
    and cross-origin fetches, so its absence means a non-browser client.
    """
    if not origin:
        return False
    return origin in allowed_origins


def demo_try_admit(ip: str) -> tuple[bool, str]:
    """Try to admit a new demo session from `ip`.

    Returns (ok, reason). On success the function has already incremented the
    concurrency counters and recorded the new-session timestamps, so a True
    result MUST be paired with exactly one later `demo_release(ip)`.

    The global concurrency cap is enforced for everyone (including exempt IPs) as
    a hard cost ceiling; per-IP concurrency and both rate windows are skipped for
    exempt IPs (local dev). `reason` is one of: "ok", "server_busy",
    "too_many_concurrent", "rate_limited".
    """
    global _demo_concurrent_total
    now = time.monotonic()
    _maybe_prune(now)

    # Global concurrency -- always enforced.
    if _demo_concurrent_total >= demo_max_concurrent:
        return False, "server_busy"

    if not _is_exempt(ip):
        # Per-IP concurrency.
        if _demo_concurrent_by_ip.get(ip, 0) >= demo_per_ip_concurrent:
            return False, "too_many_concurrent"

        # Per-IP new-session sliding window.
        ip_hits = _demo_new_by_ip.setdefault(ip, deque())
        _evict(ip_hits, now, demo_per_ip_window_s)
        if len(ip_hits) >= demo_per_ip_new_per_window:
            return False, "rate_limited"

        # Global new-session sliding window (60s) -- blunts IP rotation.
        _evict(_demo_new_global, now, 60.0)
        if len(_demo_new_global) >= demo_global_new_per_min:
            return False, "server_busy"

        # All checks passed: commit to both windows.
        ip_hits.append(now)
        _demo_new_global.append(now)

    _demo_concurrent_total += 1
    _demo_concurrent_by_ip[ip] = _demo_concurrent_by_ip.get(ip, 0) + 1
    return True, "ok"


def demo_release(ip: str) -> None:
    """Release the concurrency slot held by `ip`. Call exactly once per admit."""
    global _demo_concurrent_total
    if _demo_concurrent_total > 0:
        _demo_concurrent_total -= 1
    held = _demo_concurrent_by_ip.get(ip, 0)
    if held <= 1:
        _demo_concurrent_by_ip.pop(ip, None)
    else:
        _demo_concurrent_by_ip[ip] = held - 1


def say_try_admit(ip: str) -> bool:
    """Rate-limit typed /say turns to one per `say_per_ip_interval_s` per IP.

    Exempt IPs (local dev) always pass.
    """
    if _is_exempt(ip):
        return True
    now = time.monotonic()
    _maybe_prune(now)
    last = _say_last_by_ip.get(ip)
    if last is not None and (now - last) < say_per_ip_interval_s:
        return False
    _say_last_by_ip[ip] = now
    return True
