# Amplitude Analytics — what was built (2026-10-12)

Two planes, matching Amplitude's own split: **browser analytics** (Next.js
frontend, `@amplitude/unified`) and **Agent Analytics** (FastAPI backend,
`amplitude-ai`). Installed via Amplitude's setup wizard (browser) and the
`amplitude-ai.md` 4-phase guide (Python).

Project API key: `e4836b...fce8cd` (public by design, both planes).

## 1. Browser analytics

- **Package:** `@amplitude/unified@^1` (installed `1.1.40`) in `frontend/`.
  Unified (not analytics-only) per the wizard's exact instruction — includes
  Session Replay.
- **Module:** `frontend/src/amplitude.ts` (`"use client"`). Namespace import,
  single `initAll` guarded by `typeof window` + missing-key warning:
  ```ts
  amplitude.initAll(AMPLITUDE_API_KEY, {
    analytics: { autocapture: true },
    sessionReplay: { sampleRate: 1 },
  });
  ```
  Initialized exactly once via a null `<Amplitude />` component mounted in
  `frontend/src/app/layout.tsx` next to `<MetaPixel />`.
- **Key:** `NEXT_PUBLIC_AMPLITUDE_API_KEY` — in `frontend/.env.local` (real
  key) and `frontend/.env.example` (commented template). Build-time var.
- **First event:** `Viewed Home Page` (user's pick), fired once right after
  `initAll` in the same module (the SDK queues pre-init events), carrying
  `{ prompt_version: "BA400.4" }` — safe to remove once verified in the live
  feed. No other custom events: autocapture covers page views/sessions/
  attribution; anything more goes through `npx @amplitude/wizard`/MCP, not
  hand-rolled taxonomy.
- **Build:** `npm run build` passes (all routes prerender).

## 2. Agent Analytics (backend)

- **Package:** `amplitude-ai>=1.17.0`, pinned in `pyproject.toml` (`uv add`).
- **Key:** `AMPLITUDE_AI_API_KEY` — in root `.env` (real key) and
  `.env.example` (template). Read lazily at first use (`config/settings.py`
  already `load_dotenv()`s at import).
- **Bootstrap:** `agents/amplitude_ai.py` — module-level `AmplitudeAI`
  singleton (`content_mode="metadata_only"`, see §4), four module-level
  agents (singletons — per-request agents would break session grouping):
  `conversation-partner` (live voice turns), `drill-judge` (verdicts on
  learner answers), `content-forge` (generated content + verify passes),
  `post-session` (disconnect/background harvesters). Everything fail-open:
  no key → no-op after one warning; tracking never breaks a drill/turn/boot.
- **One-shot choke point:** `DeadlineChatOpenAI._agenerate`
  (`agents/openrouter_llm.py`) — the single async hook all ~35 judge/forge/
  debrief calls route through. Times the call, extracts
  `usage_metadata` + verdict text from the `ChatResult`, emits a user+AI
  pair bound to the active session contextvars (skips when no session is
  open — unbound events join nothing), re-raises on error after emitting an
  `is_error` turn. Canonical model id `gpt-oss-120b` + gateway context
  (`cerebras-direct` vs `openrouter`) on every turn.
- **Conversation turns:** `ClientWrapper.astream`'s `finally`
  (`agents/pipecat_wrapper.py`) emits one pair per streamed turn with
  explicit binding (`user_id`, bare-hex `session_id`, per-exchange
  `trace_id`), since pipeline tasks may not inherit the factory's
  contextvars. Synthetic inputs (kickoff/exercise-result/wrap-up) flagged in
  context, never hidden.
- **Session frames:** `pipeline/factory.py` opens a `conversation-partner`
  session at connect (same-task enter/exit via a plain handle) and a nested
  `post-session` frame around the disconnect `asyncio.gather` (contextvars
  propagate into gather children, so the hook binds harvesters correctly);
  exit + `flush_ai()` next to the trace-context detach.
- **HTTP drills:** one-line `ai_session(...)` appended to the existing
  Langfuse span `with` lines in every LLM-touching handler — verdict
  handlers → `drill-judge`, generation handlers (writer, gloss, explain,
  nudges, rephrase, forge) → `content-forge`, background harvests →
  `post-session`. Session id reuse: the same frontend-minted sitting id the
  Langfuse spans already carry (prefix-normalized: `tandem-/teacher-/
  lesson-/demo-` stripped to the bare hex so Clara's routes join the WS
  sitting). No stable id → skip (never invent one).
- **Feedback:** `POST /satz/flag` mirrors the disagree-flag via `ai.score`
  (`user-feedback`, session target), fail-open.

## 3. Verification evidence

- `ruff check` clean on all 17 touched files; backend import smoke
  (`scripts/ci_smoke.py`) 19/19 routers; `amplitude-ai-doctor` all green.
- Live `faelle/judge_case` (reference answer → `correct: True`) inside a
  session: verdict intact with the hook active.
- Delivery: `on_event_callback` showed 3× HTTP 200 `Event sent successfully`
  (user + AI + session end). The `test-verify` sessions in the project are
  these probes — deletable.
- Browser side unverified by us: needs a human page load → `Viewed Home
  Page` in the Setup live feed (see §5).

## 4. Deliberate tradeoffs

- **`metadata_only`, not `full`.** Token counts/latency/model/cost flow;
  prompt/response text does not. Langfuse already owns content traces;
  Amplitude answers "how is it used". Helpers still pass real texts (dropped
  server-side), so flipping modes needs no call-site change.
- **Manual tracking, no provider wrappers.** All LLM traffic goes through
  OpenAI-compatible gateways (Cerebras-direct, OpenRouter) via LangChain —
  no `amplitude-ai` wrapper exists for that hop. Usage is read from the
  LangChain response; cost auto-calculates from the canonical model id (if
  `Cost USD` is empty in the UI, genai-prices doesn't know `gpt-oss-120b`
  and explicit pricing is the fix).
- **No multi-agent delegation.** Verified: no tools/function-calling, no
  parent-calls-child-LLM patterns (forge draft→verify loops are sequential
  within one function). Single-agent sessions throughout, no `run_as`.
- **Known gaps (documented, not silent):** background forge backfill
  (`backfill_missing`, no sitting id) and Clara's catalog-deal span (mostly
  no-LLM; developer forge-deals via `get_exercise` unattributed) emit
  nothing; forge content generated inside a judged attempt attributes to
  that attempt's judge session; give-up/deterministic-green attempts yield
  zero-message sessions (mirrors the repo's own Langfuse philosophy: a
  sitting shows every attempt, not just token-costing ones); `user_id`
  `"0001"` (4 chars) may fall under Amplitude's 5-char minimum.

## 5. To ship

- [ ] Human loads `/` → `Viewed Home Page` in Setup live feed (browser proof).
- [x] Railway vars set (2026-10-12): `AMPLITUDE_AI_API_KEY` (backend,
      runtime) + `NEXT_PUBLIC_AMPLITUDE_API_KEY` (frontend, build-time).
- [x] `frontend/Dockerfile` carries `ARG/ENV NEXT_PUBLIC_AMPLITUDE_API_KEY`
      (same pattern as `META_PIXEL_ID`) — the rebuild bakes the key in.
- [x] Prod CSP allows `https://api2.amplitude.com` in `connect-src`
      (`next.config.ts`; US endpoints — live 200s proved the default).
      No new `script-src` host: the npm bundle loads no external script.
- [ ] Push → both services rebuild → verify below.
- [ ] Confirm project region (browser init defaults to US endpoints; if the
      project is EU-hosted, both planes need the EU endpoint).
- [ ] Privacy page: Amplitude is non-essential tracking like the Pixel —
      needs the consent banner + §§2.5/3/4 update before it matters
      (the Pixel gap documented there applies here too).
- [ ] Remove `prompt_version` from the `Viewed Home Page` call and delete
      the `test-verify`/`verify-session-*` probe sessions once done.
