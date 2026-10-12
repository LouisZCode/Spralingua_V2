# Scale tests — the ladder

Goal: prove the backend holds N concurrent learners, in small steps, cheapest test first.
Each rung passes before the next is attempted. Results are logged at the bottom.

## Ground rules (every rung)

- **Local only, never Railway prod.** Local backend `:8765` or an isolated worktree stack. Prod has real coins, real provider bills, real learners.
- **Fake providers for the HTTP ladder** (same mechanism LOAD-001 used — 2s fake delay). We measure our stack, not Cerebras's. Real providers only at rung T4, and voice only at T5+.
- **`test-*` users, `role: "developer"`** (bypasses every coin gate, no ledger rows), backend started with `SPRALINGUA_TEST_GUARD=1` (a stray write to a real account fails loud).
- **Amplitude key unset** in the load-test env — k6 iterations must not eat the 2M/month event quota.
- **Cost:** rungs T1–T3 ≈ €0. T4 = cents. T5+ = lunch money (voice ≈ $0.45/learner-hour, TTS-dominated).

## k6 in 30 seconds (the whole mental model)

- `export default function()` = one virtual user's loop. `export const options` = load shape.
- `stages: [{duration, target}]` ramps VUs up/down. `check()` asserts per request, `thresholds` pass/fail the run (this is what gates rungs).
- `setup()` runs once (sign in test users, hand JWTs to VUs). `sleep(n)` models think time — learners read cards ~20–30s; without it you're testing an unreal flood.
- Install: `brew install k6`. Run: `k6 run speedtest/k6/t1-drills.js`.

## The ladder

| Rung | Load | Fidelity | Question it answers | Gate to next |
|---|---|---|---|---|
| T1 | 10 VUs, HTTP drills | fake providers | Does the baseline hold a classroom? | p95 reads < 500ms, errors < 1% |
| T2 | 50 VUs, HTTP drills | fake providers | Where does the pool start to bend? (watch 5/5 local vs 20/20 prod) | same thresholds |
| T3 | 100 VUs, HTTP drills | fake providers | Headroom for thousands of registered users | same thresholds, no 5xx |
| T4 | 10 VUs, HTTP drills | REAL providers | Judge latency/cost under concurrency (cents) | p95 attempts < 12s leg budget |
| T5 | 3–5 concurrent voice sessions | real, scripted turns | Does one worker hold a handful of talkers? | clean audio, no drops |
| T6 | 10 concurrent voice sessions | real | Practical voice ceiling of this architecture | decided by ear + metrics |

100 concurrent *voice* is explicitly out of scope: single `uvicorn` worker + in-memory session state + ~$30+/hr STT at that scale. That needs replicas + sticky sessions — a "good problem" milestone, not this ladder.

## P1 — prod smoke (runs only after T1 is green)

- **Why:** Railway sizing signal (vCPU/RAM spike per concurrent learner) + real provider latency. Local T1 validates the script; P1 measures prod.
- **Blast radius accepted 2026-10-12:** no real learners yet, real provider spend OK (cents), Amplitude quota burn OK (test traffic excluded in charts via `test-*` user filter).
- **Script:** `speedtest/k6/p1-prod-smoke.js` — same endpoints as T1, `give_up=true` attempts only (no coins, no ledger rows).
- **Load:** 1 VU × 1 min (script validation), then stages `30s→3 VUs`, `2m hold`, `30s→0`. Off-hours, Sentry + Railway metrics watched live.
- **Never on prod:** voice sessions, payment paths, real (non-`test-*`) accounts.
- **Rate limits:** single-machine k6 = single IP — SEC-001/`/say` gates will 429 sooner than capacity bends. Expected; note where throttling starts, it calibrates the gates too.

## T1 — first rung (fully specified)

- **Script:** `speedtest/k6/t1-drills.js` (to be written; `speedtest/` is gitignored, stays local).
- **Endpoints:** `GET /health` (sanity), `GET /faelle/round` (cheap read — LOAD-001's probe), then `POST /faelle/submit_attempt` with `give_up=true` (exercises the write path, costs nothing, writes no ledger row).
- **Setup:** seed 10 `test-developer-*` users (developer role), sign in once in `setup()`, one JWT per VU.
- **Load:** stages `30s→10 VUs`, `3m hold @10`, `30s→0`. `sleep(20 + random*10)` between iterations.
- **Thresholds:** `http_req_failed < 1%`, `http_req_duration p95 < 500ms` (reads).
- **Watch during run:** 100ms pool sampler (held conns — the LOAD-001 signal), uvicorn log for 5xx/429/402 (402 = gate misconfig, must be zero for developers), p50/p95 per endpoint.
- **Pass:** thresholds green + no errors + pool never pinned. Record below, then T2.

## Runbook (every rung)

1. `brew install k6` (once).
2. Start local backend with guard: `SPRALINGUA_TEST_GUARD=1 uvicorn main:app --port 8765`.
3. Seed users: `scripts/test_user.py create test-developer-N --profile beginner` (×N, developer role).
4. `k6 run speedtest/k6/<rung>.js`.
5. Log results below. Destroy users: `scripts/test_user.py destroy test-developer-N`.
6. Advance rung only on green.

## Results log

| Date | Rung | VUs | p50/p95 | Errors | Pool notes | Verdict |
|---|---|---|---|---|---|---|
| 2026-10-12 | P1 prod smoke | 1→3 | reads 51/66ms, all 48/84ms | 0/66 | n/a (no sampler; one 453ms max, likely cold start) | PASS — script valid, prod holds 3 learners effortlessly |

## P1 field notes (2026-10-12, keep for T2/T3)

- Prod Postgres is `postgres-z4hi.railway.internal` — unreachable from outside. Seeding needed the **temporary public TCP proxy** (Postgres → Settings → Public Networking) + `DATABASE_PUBLIC_URL`, which lives on the **Postgres service vars**, not the backend's. Backend service: `backend`, backend host `backend-production-a6c2.up.railway.app`.
- Seeder/mint/destroyer: `/tmp/seed_load.py`, `/tmp/destroy_load.py` (throwaway, secrets only in process memory). Pattern: pull `railway variables --kv` for both services → `init_engine(config.settings.database_url)` (normalized to asyncpg) → `test_user.create` → SQL promote `role='developer'` → `issue_session_jwt(uid, role='developer')` with **prod** `JWT_SECRET` → tokens to `speedtest/k6/.tokens.json` (0600).
- Confirmed live: developer role bypassed all coin gates (zero 402s), rate gates stayed quiet at 3 VUs, `give_up=true` attempts wrote `:giveup` rows with no ledger trace (then cascaded on destroy).
- Cleanup done 2026-10-12: `test-load-1..3` destroyed, prod verified 0/0/0 across `users`/`drill_attempts`/`coin_ledger`.
- **Luis to-do:** disable public proxy + rotate `PGPASSWORD` (old one transited local logs; rotation restarts the DB — redeploy backend if it throws after).

## History

- LOAD-001 (2026-09-04): 10-user baseline — default 5/5 pool pinned 57% of run, `/faelle/round` 463ms p50 vs 5ms at 20/20. Prod pool since raised to 20/20. T1 re-measures this with the fix in place.
