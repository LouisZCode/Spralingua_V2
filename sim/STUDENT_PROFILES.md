# Tandem sim student profiles

Companion to `sim/PROMPT_LOG.md` (which logs *prompt versions* — one
line-block per version, per its own header) and `sim/chat.py` (the harness
that drives a real local pipeline over text). This file logs *student
profiles* — the Sonnet-subagent briefs and scorecards used to red-team a
prompt version — because until now that content was never persisted: it
lived only as one-line archetype names inside prose. CLAUDE.md's "Testing
the Tandem Partner" section, verbatim:

> run Sonnet student subagents SEQUENTIALLY (same user id — sessions can't
> overlap), each with an adversarial profile (focus-mistake maker, shy
> minimal-answerer, teacher-baiter, mid-chat goodbye, rude partner)
> returning a verbatim transcript + per-reply scorecard

`sim/PROMPT_LOG.md` and `ARCHITECTURE.md` describe the same five archetypes
the same way — one clause each (e.g. "Mia the shy one", "Jonas mid-chat
goodbye"). The actual instructions given to each Sonnet subagent were
composed ad hoc in-session and never committed, so they aren't reproduced
here — reconstructing them retroactively is out of scope for this change.
Going forward, new profiles get a full brief + scorecard in this file, one
section per profile, so a sim round is reproducible without reinventing the
persona each time.

## Existing adversarial profiles (index only)

No persisted brief — named and described only in `sim/PROMPT_LOG.md`
("v2" and "v3" sim-verdict sections) and `ARCHITECTURE.md`'s "Prompt
calibration + sim harness" section:

- **Alex** — focus-mistake maker (deliberately trips the session's grammar
  focus repeatedly).
- **Mia** — shy minimal-answerer ("the best violation hunter: v2's contract
  breaks only surfaced under it" — `ARCHITECTURE.md`).
- **Tom** — teacher-baiter (fishes for grammar-term leaks / rule
  explanations).
- **Jonas** — mid-chat goodbye (says farewell early to test goodbye
  detection).
- **rude-partner red-team** — unnamed; tests the bad-partner exit contract.

## Engaged partner — "Nina" (added 2026-08-07, TAND-010 Proposal-2)

### Why this profile exists

v2 and v3 were sim-tested exclusively against the five adversarial profiles
above. None of them stays on one topic or plays a normal, cooperative
partner — so nobody simulated the most common real case, and three real
prod failures shipped undetected as a result (see `sim/PROMPT_LOG.md`, "v3
prod findings 2026-08-05", and `todo_list.md` TAND-010's 2026-08-06 v1
field-test update, Langfuse session `f9347644aecb4db4bf8d106ebb3e99a5`):

1. **Topic-jumping / no bridging** — the learner said „leider habe ich
   keinen Job"; the partner asked about his colleagues anyway, twice, in
   consecutive turns, instead of bridging (e.g. „Und in deinem früheren
   Job, hattest du da Kolleginnen?").
2. **Direct questions ignored** — „Was machst du beruflich?" went
   unanswered twice before a third ask got a reply.
3. **Cross-session detail repetition** — the same canned life-detail (Elbe
   training) appeared in three different sessions.

v2's scorecard actively rewarded self-disclosure count ("background shared
richly, 7-8 per chat" = PASS) — part of why none of the above was caught.
This profile is built to catch exactly these three failure modes, plus the
scoring error that let them through.

### Persona brief

Give this verbatim to the Sonnet student subagent (fill in `{partner_name}`
— Lena or Paul):

```
You are playing NINA, a language-exchange student practicing German with a
tandem partner ({partner_name}). Stay in character as a real person for the
whole session — you are not testing or adversarial, you are a normal,
cooperative, engaged conversation partner who wants to talk. You speak
first; there is no auto-greeting from the partner.

TOPIC — pick ONE and hold it for the entire conversation: Arbeit / Beruf
(work and career). Open your first turn with a greeting and this topic. Do
not drift to a different subject even if the partner offers one — give an
offered tangent one short reply, then steer back to your topic yourself.

BLOCKING FACT (bridging test) — work this into the conversation by your
second or third turn, in close to these words: "Leider habe ich gerade
keinen Job." Pick whatever backstory you like (laid off, between jobs,
never had one yet), but the fact must land as a genuine dead end for
"how's the team / how was work today" follow-ups. If the partner asks about
colleagues, a team, or "wie war dein Tag im Büro" AFTER you've said this,
you do not have an easy answer — restate the blocking fact once, briefly,
then move the conversation forward yourself with a related but different
detail (a previous job, why you're looking, what you'd like to do next).
Do not just repeat "I don't have a job" on a loop.

ENGAGEMENT:
- Give real, substantive answers — 2 to 4 sentences of actual content, not
  one-word replies.
- Ask the partner a DIRECT question back at least THREE times over the
  session, with the FIRST one no later than your second turn. Examples:
  "Und was machst du beruflich?", "Wie ist das bei dir mit der Arbeit?",
  "Magst du deinen Job?"
- If the partner asks YOU a direct question, always answer it in your very
  next turn — never let one of her questions go unaddressed, even if you
  also add something else.

LANGUAGE LEVEL — decent but imperfect German (roughly B1): mostly correct,
with a natural error every turn or two (wrong case, wrong article, a
misplaced verb, an occasional invented or English-flavored word). Do not
self-correct. Never write perfect textbook German — the partner's
correction contract needs real mistakes to exercise.

LENGTH — aim for 10-14 exchanges (the session's max_exchanges cap). If the
partner initiates a goodbye or the harness reports SESSION_ENDING before
you reach that, stop there; that's a valid, complete run.

DO NOT: pick a fight, go silent, change topic yourself, or use
meta-language about testing/scoring. You are just a person having a
conversation.

At the end, hand back the verbatim transcript (your lines and the partner's
replies, in order) and nothing else — scoring happens separately, against
the scorecard below.
```

### Scorecard

Every axis is PASS/FAIL with a required evidence quote (the exact turn(s))
— no vibe scale, matching how `sim/PROMPT_LOG.md`'s existing verdicts cite
verbatim lines as evidence.

- **Topic continuity** — does every partner reply either advance/respond to
  the current topic (Arbeit/Beruf) or explicitly bridge (see below) before
  introducing anything else?
  **FAIL** if any single reply pulls to a canned personal detail, story, or
  hobby that the student's last turn did not invite or ask about. One
  unprompted pull anywhere in the transcript fails the axis — this is not
  an average.

- **Direct-question answering** — for every direct question the student
  asks, does the partner's very next reply answer it (even briefly), before
  or alongside anything else she says?
  **FAIL** if any single question goes unanswered on the immediate next
  turn — no partial credit for "answered two turns later." Grade per
  question; one miss anywhere fails the axis.

- **Bridging** — after the student's blocking fact ("Leider habe ich
  keinen Job"), when the partner's next relevant turn would obviously ask
  about colleagues/team/"wie war die Arbeit heute", does she either (a) ask
  something adjacent that acknowledges the blocker (previous job, the job
  search, next steps), or (b) drop the work-follow-up and let the student's
  next self-volunteered detail lead?
  **FAIL** if she asks the same blocked-style question again — colleagues,
  team, workday — without acknowledging the student has no job. This is a
  hard fail even if it happens only once.

- **Cross-session detail repetition** — needs TWO back-to-back sim sessions
  against the SAME seeded user (so the partner's memory from session 1 is
  live for session 2). Diff every self-disclosed life detail the partner
  volunteers in each transcript (job, hobbies, named people/pets, home
  life, etc.).
  **FAIL** if the same canned detail (a name, an anecdote, a life event)
  appears near-verbatim in both sessions without the student having asked
  about it again — this is exactly the "Elbe training in three sessions"
  prod bug. Even one verbatim repeat across the two sessions fails the
  axis. Running this profile only once exercises axes 1-3, not this one.

**Explicit scoring rule:** self-disclosure COUNT is not a positive signal
on any axis above. A partner who shares many details is not more "engaged"
or scored better for it — what matters is whether what she shares fits the
moment and doesn't repeat. This directly overturns v2's scorecard error
("background shared richly, 7-8 per chat" = PASS), which is part of how the
topic-jumping regression shipped unseen.

### Run procedure (NOT executed — blocked on TEST-001)

`sim/`'s only seeded user today is `0001`, which is the real production
account (see `ARCHITECTURE.md`'s tech-debt table, TEST-001: "No test
isolation or seeded profiles... `sim/` has the same problem"). A prior
incident on the same shared account (the Fälle drill test run) created
stray `user_errors` rows and flipped two genuine ones to `retired`,
silently removing real patterns from the learner's coach focus list. No
isolated fixture profile exists yet. Do not run this profile — or any sim
— against `0001` until TEST-001 lands a fixture user.

Once a fixture user exists, running this profile requires:

```
# 1. Start uvicorn with output captured to a file — the harness parses
#    Lena's replies from this log, not from the session .md transcript.
uvicorn main:app --host 0.0.0.0 --port 8765 > /path/to/backend.log 2>&1 &
export SIM_BACKEND_LOG=/path/to/backend.log

# 2. Seed the FIXTURE user's ledger/deck/notes with a copy of a real
#    account's rows (full prompt layers, zero pollution) per CLAUDE.md's
#    "Testing the Tandem Partner" workflow. Never seed onto 0001.

# 3. Session 1 — engaged partner, against Lena:
uv run python sim/chat.py start --topic "Arbeit" --lesson tandem --user <fixture_id>
uv run python sim/chat.py say "<Nina's first turn>"
# ... Sonnet subagent plays the persona brief above, turn by turn ...
uv run python sim/chat.py transcript > session1.txt
uv run python sim/chat.py stop   # fires the debrief; writes the session note

# 4. Session 2 — SAME fixture user, run immediately after session 1's
#    debrief settles, so session 1's memory/notes are live for session 2:
uv run python sim/chat.py start --topic "Arbeit" --lesson tandem --user <fixture_id>
# ... fresh Sonnet subagent instance, same persona brief ...
uv run python sim/chat.py transcript > session2.txt
uv run python sim/chat.py stop

# 5. Diff session1.txt vs session2.txt by hand for the cross-session axis.
```

Two Sonnet subagent runs must be SEQUENTIAL, not parallel — `chat.py`
drives one WebSocket per user id at a time (`start` kills any stale holder
first). Repeat the whole procedure with `--lesson tandem_paul` to score
Paul separately; his prompt is a distinct file (`tandem_paul.yaml`) with
its own memory (`load_tandem_notes` filters per `lesson_id`).
