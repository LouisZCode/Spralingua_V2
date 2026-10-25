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
  feed. Autocapture covers page views/sessions/attribution; the product
  semantics below are the hand-rolled taxonomy (deliberately small — see
  the P1 section).
- **Build:** `npm run build` passes (all routes prerender).

## 2. P1 semantic events — the product taxonomy (2026-10-25)

Round-level product events so Amplitude answers *how learners use the app*,
not just how they arrive and pay. All client-side via
`amplitude.ts::trackEvent` — consent-gated by construction (the SDK only
initializes after consent; pre-grant and post-revoke calls are no-ops, the
same LEGAL-003 promise the pixel obeys), never throwing into app code, and
riding the init promise so an event fired in the same tick as the grant
still lands. **Pre-consent events queue instead of dropping** (2026-10-25):
a signup can beat the non-blocking banner, so `trackEvent`/
`setUserPropertiesOnce`/`trackFbq` park pre-consent calls and flush the
moment consent is granted — a decline leaves them unsent, which is the
legal contract. Per-item drill detail deliberately stays DB-only
(`drill_attempts` is the exact record); Amplitude carries the sitting's
shape. Props are snake_case to match the existing server events
  (`price_id`, `revenue_type`, `prompt_version`).

| Event | Props | Fires from |
|---|---|---|
| `Sign Up Completed` | `utm_source` · `utm_medium` · `utm_campaign` · `utm_content` · `utm_term` (present when the landing carried them; absent props are omitted, not empty strings) | `StartCta.tsx` on a server-confirmed `isNewUser` signup — the same moment that fires the Pixel's `CompleteRegistration`. The UTMs come from `lib/utm.ts` (parked at first landing, before the Google redirect can wipe them) and are ALSO written as Amplitude **user properties** (`setOnce`, first-touch) so every later event — including the server-side `Coins Purchased` — is attributable to the ad without a join |
| `Mode Opened` | `mode`: satz · flow · tandem · briefkasten · clara | `PracticeMenu.tsx` card click (incl. the recommendation banner; the dev-only interview card fires nothing) |
| `Round Started` | `mode`, `rounds`, `cost` (coins) | the three pickers' Start (`Flow.tsx`, `Satzschmiede.tsx`, `Briefkasten.tsx`) — voice surfaces have no picker, their "start" is `Voice Session Started` |
| `Round Completed` | `mode`, `items`, `correct`, `skipped` (flow only), `target` (flow), `duration_s` | the three drill end screens — auto-finish AND manual Finish (once-guarded per sitting; satz `correct` = cards left the queue by passing, miss-out cards count in `items` only; briefkasten carries just `items` + `duration_s`) |
| `Voice Session Started` | `mode`: tandem · teacher · lesson, `topic`, `exchanges_chosen` | `ConversationView.tsx` at the `session_started` handshake — past every gate, so rejected/dead connects fire nothing |
| `Voice Session Ended` | `mode`, `ended_by` (user · agent), `exchanges` (revealed bot turns), `duration_s` | `ConversationView.tsx::handleFinish` — before the user-branch return, guarded by the start ref (no session, no event) |

Funnel that now exists: landing → signup → `Mode Opened` → (`Round
Started` | `Voice Session Started`) → (`Round Completed` | `Voice Session
Ended`) → purchase. `mode` values match the streak/mode vocabulary
(`daily_mode_completions`), so DB cross-references stay one join.

**Not tracked, on purpose:** the nine dev-door drills standalone (no real
learner route), the demo socket (no identity), and per-item attempts
(server events there would bypass consent; the DB already owns that detail
— Proposal-2 in ARCHITECTURE.md's Marketing Analytics section if ever
needed).

## 3. Agent Analytics (backend)

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

## 4. Verification evidence

- `ruff check` clean on all 17 touched files; backend import smoke
  (`scripts/ci_smoke.py`) 19/19 routers; `amplitude-ai-doctor` all green.
- Live `faelle/judge_case` (reference answer → `correct: True`) inside a
  session: verdict intact with the hook active.
- Delivery: `on_event_callback` showed 3× HTTP 200 `Event sent successfully`
  (user + AI + session end). The `test-verify` sessions in the project are
  these probes — deletable.
- Browser side: verified 2026-10-12 — headless Chromium against prod
  (`speedtest/launch-audit-2026-09-02/env/_amplitude_prod_check.mjs`)
  captured `POST api2.amplitude.com/2/httpapi → 200, 5 events ingested`,
  and an MCP query the same day read the landed data back: `Viewed Home
  Page` — 3 events/24h, all with `prompt_version: BA400.4`.

## 5. Deliberate tradeoffs

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

## 6. To ship

- [ ] Human loads `/` → `Viewed Home Page` in Setup live feed (browser proof).
- [x] Railway vars set (2026-10-12): `AMPLITUDE_AI_API_KEY` (backend,
      runtime) + `NEXT_PUBLIC_AMPLITUDE_API_KEY` (frontend, build-time).
- [x] `frontend/Dockerfile` carries `ARG/ENV NEXT_PUBLIC_AMPLITUDE_API_KEY`
      (same pattern as `META_PIXEL_ID`) — the rebuild bakes the key in.
- [x] Prod CSP allows `https://api2.amplitude.com` in `connect-src`
      (`next.config.ts`; US endpoints — live 200s proved the default).
      ~~No new `script-src` host: the npm bundle loads no external script.~~
      **Corrected 2026-10-12 (`1ceafea`, headless-Chromium prod run caught
      both as enforced-CSP violations):** the unified package IS NOT purely
      an npm bundle — it lazy-loads the engagement/autocapture bundle from
      `cdn.amplitude.com` (script-src; the old claim was wrong), fetches
      Session Replay's remote config from `sr-client-cfg.amplitude.com`
      (blocked => "Failed to generate joined config" + degraded replay),
      uploads replays to `api-sr.amplitude.com`, and reports to
      `diagnostics.prod.us-west-2.amplitude.com`. All four now allowlisted;
      the re-run shows zero violations, the engagement chunks load, replay
      uploads (`?type=replay`), and ingestion is 200/5 events.
- [x] Push → both services rebuilt → verified live 2026-10-12:
      `POST api2.amplitude.com/2/httpapi → 200, 5 events ingested`.
      Human dashboard confirmation (Setup-page live feed) still pending.
      Scratch verifier: `speedtest/launch-audit-2026-09-02/env/
_amplitude_prod_check.mjs` (headless Playwright, gitignored).
- [ ] Confirm project region (browser init defaults to US endpoints; if the
      project is EU-hosted, both planes need the EU endpoint).
- [ ] Privacy page: Amplitude is non-essential tracking like the Pixel —
      needs the consent banner + §§2.5/3/4 update before it matters
      (the Pixel gap documented there applies here too).
- [ ] Remove `prompt_version` from the `Viewed Home Page` call and delete
      the `test-verify`/`verify-session-*` probe sessions once done.
- [x] Bot traffic inflates uniques — fixed 2026-10-12 (ANALYTICS-001 /
      ANALYTICS-002, lint + build green, unshipped): `?probe=1` pins the
      headless verifier to device `probe-headless` (was: one fresh anonymous
      user per run); `identifyAnalyticsUser(AuthUser.id)` fires on sign-in
      AND on localStorage hydration, `clearAnalyticsUser()` on sign-out —
      browser events now stitch to the same `users.id` the agents stamp
      server-side. Found via MCP: 3 headless "users" + backend turns under
      `test-verify` and the Google-ID user. The 3 historic probe users stay
      in history (not retro-merged).

## Query it via MCP (any agent, added 2026-10-12)

The Amplitude MCP server lets any MCP-capable agent read this project's
events, charts, sessions and agent data directly — no dashboard clicking.
Connected and verified live 2026-10-12 (org `cool-hat-831508`, project
`default` = `877530`).

- **Server URLs:** US default `https://mcp.amplitude.com/mcp`;
  EU residency `https://mcp.eu.amplitude.com/mcp`. This project ingests
  at the US default — use the US URL.
- **Requirements:** client must support remote MCP servers over Streaming
  HTTP **plus OAuth 2.0** (API-key-only or stdio-only clients are
  incompatible).
- **opencode** (`~/.config/opencode/opencode.jsonc`, global scope — personal
  access, never the project config):
  `mcp: { "amplitude": { "type": "remote",
  "url": "https://mcp.amplitude.com/mcp", "enabled": true } }`.
  Quit + restart opencode (no hot-reload), approve the browser OAuth on
  first use, restart once more if tools don't surface (tool list loads at
  startup, pre-auth). Verify with: *"What Amplitude projects can I
  access?"* → expect `default (877530)`.
- **pi** (`~/.pi/agent/mcp.json`, user level — personal OAuth-credentialed
  server, never project-local):
  `{ "amplitude": { "url": "https://mcp.amplitude.com/mcp",
  "description": "Amplitude Analytics: query live events, charts, dashboards
  and Agent Analytics data for the Spralingua project" } }`.
  Sign in via `pi mcp login amplitude` (browser approval; tokens in
  `~/.pi/agent/mcp-auth.json`, auto-refresh; sign out via
  `pi mcp logout amplitude`). Connected 2026-10-12: **49 tools**
  (codemode exposure — reached via `searchTools()`/`describeTool()` in
  codemode scripts, not declared per prompt) + 34 resources. Same verify
  question → `default (877530)`. Agent Analytics queries:
  `get_amplitude_agent_analytics_info` / `get_agent_results`.
- **Unlisted clients:** add the URL as a remote MCP server per the client's
  own docs, OAuth with the Amplitude account, enable the needed tools.
- **Standard checks once connected** (all take `projectId: "877530"`):
  `get_amplitude_context` (no args = list projects), then
  `search_amp_data_taxonomy` for exact event names (`Viewed Home Page`,
  `[Amplitude] Page Viewed`, `[Agent] User/AI Message|Response|Session
  End`), then `query_amplitude_data` (typed `chart`, `date_range`
  required) for counts — e.g. today's prod visit read as 2× home page,
  2× page viewed, 4× `drill-judge` turns.
- **Agent Analytics reads:** Monitor/Sessions/Evaluators views via
  `get_agent_results` (`agent_type: dashboard_explorer |
  session_replay_explorer`); our 4 agents are `conversation-partner`,
  `drill-judge`, `content-forge`, `post-session`, all `metadata_only`
  (costs/tokens/latency visible, no transcripts — by design).
- **ALWAYS read the clock before any time-windowed MCP query
  (2026-10-12 rule — two wrong windows in one evening each returned
  confident zeros).** Run `date -u +%s` (+ `date -u`) first and derive
  every epoch from it — never from memory, never from a requestId
  prefix. `query_amplitude_data` takes absolute epoch seconds only
  (relative strings are rejected) and returns all-zero series for
  future or empty windows without erroring, so a wrong epoch reads
  exactly like "tracking is broken". Corollaries: chart aggregates
  lag Live Events by minutes — for fresh activity read the user
  timeline (`get_amp_user_data` `include: timeline`) or Live Events,
  and use charts only for settled windows; `use_amplitude_cohorts`
  `find` does not strictly honor `timeRange`, so confirm recency
  from event timestamps, not from membership.
