# Lena prompt iteration log (TAND-002 / TAND-001)

One line-block per prompt version. The YAML header comment in
`agents/prompts/tandem.yaml` names the current version; each version is one
commit on the `lena-calibration` branch. Sims run via `sim/chat.py` with
Sonnet student agents; verdicts come from transcript + Langfuse review.

## v1 — baseline (pre-2026-07-27)

The shipped prompt as of `main`. Known failures (2026-07-27 prod test,
Langfuse session `cea26036…`): monster replies (2,442 output tokens on one
turn), vocab crammed ~6 words into single sentences, grammar-term leaks
("Reflexiv-Verb"), parenthetical study advice, raw `**markdown**`, stale
"12–14 exchanges" wrap-up wording, interrogation feel (TAND-001).

## v2 — hard limits + no-teacher negatives + a life (2026-07-27)

- HARD RULE: max 2 sentences per reply, max ONE question.
- Vocab: fetch capped 10 → 7 (`pipeline/factory.py`); header rewritten "at
  most one or two per reply, fine if several never come up" (was "work EVERY
  one of them in").
- NEVER block: no grammar words in any language (notes are for her eyes
  only; rule-explaining requests get a natural example instead), no study
  advice, no bracket asides, no pointing at non-focus mistakes.
- Focus-fix example de-termed: "Ah, mit 'weil' klingt es so: …" (was "'weil'
  schickt das Verb ans Ende" — used the word "Verb").
- Wrap-up: never initiate goodbye; respond warmly when the partner starts it
  (matches TAND-004: End button + cap-30 backstop).
- Markdown stripped from the templates themselves (`**{topic}**` → `{topic}`,
  `*den*` → plain) + explicit "plain spoken text, read aloud" rule.
- TAND-001: "Your life right now" background block (bookshop takeover dream,
  Elbe cycling tour with Paula, failing Chatschapuri, Bruno the cat, small
  opinions) + "a friend shares; an interviewer only asks" rule.

Sim verdict (3 Sonnet student sims, sessions 006-008 of 2026-07-27 — Alex
the focus-mistake maker / Mia the shy one / Tom the teacher-baiter):

HOLDS: 2-sentence cap ~100% (one 3-sentence greeting), ≤1 question all but
twice, ZERO markdown/emoji/brackets, no wrap-up initiation ever, background
shared richly (7-8 self-disclosures per chat) and consistent (Paula/shop/
Elbe/Bruno), focus-slip ladder works (silent recast → echo-nudge → explicit
fix; student self-corrected twice).

FAILS → v3 candidates:
1. Non-focus contract broken under shy-student conditions: article slips got
   echo-corrections ("Gouda, mit dem Brot?", "Mit dem Zucker?") and one
   delayed forbidden "Meintest du „mit der Milch"?" — the nudge format
   generalized beyond the focus list.
2. Interrogation rhythm: 26 of 27 substantive replies end in a question —
   the "every few turns skip the question" rule never fired once.
3. Vocab weaving collapsed to ~zero (only "schön"; 0/7 in two sims) — the
   softened dose rule overshot.
4. One grammar-term leak under a direct rule request ("Nebensatz", "Verb")
   — recovered term-free when pushed harder.
5. Stale echo-nudge: "Weil ich …?" repeated on an already-correct sentence.

CODE-SIDE (not the prompt): goodbye detection arms at max_exchanges-1 = 29
(pipecat_wrapper `_goodbye_after`), so mutual goodbyes ended NO sim session
— TAND-004's 14→30 raise silently disabled goodbye-driven endings. Debrief
note misattributed Lena's Elbe training to the learner (session 006 note).

## v3 — focus-only nudges, question brake, real goodbyes (2026-07-27)

Targets the five v2 fails + activates goodbye endings:
- Echo-nudge explicitly scoped to focus structures ONLY (ladder step 1 +
  hardened NEVER bullet quoting the v2 violations "Mit dem Zucker?" /
  "Meintest du …?" as counter-examples).
- Question brake: two question-ended replies in a row → the next reply must
  contain NO question (was the toothless "every few turns").
- Vocab soft target: "aim to work two or three in over the whole chat"
  (v2's "fine if none come up" collapsed usage to ~zero).
- Rule requests get a dedicated bullet with a model answer ("Hm, Regeln
  kenne ich nicht, ich sag's einfach so: …") — v2 leaked "Nebensatz"/"Verb"
  on the first direct ask.
- Nudge one-turn lifetime (ladder step 4) — kills the stale "Weil ich …?"
  repeat on an already-correct sentence.
- NEW: "If it goes badly" block — one calm boundary, then a firm goodbye
  ends the chat with an abusive partner.
- CODE: `goodbye_after` per-lesson YAML override in pipecat_wrapper;
  tandem.yaml sets 3 — farewell in Lena's reply from exchange 3 on ends the
  session (mutual goodbyes work again; the exit above actually exits).
  Prompt adds: farewell words are never casual filler mid-conversation.

Sim verdict (3 sims, sessions 009-011 of 2026-07-27 — Mia-shy retest /
Jonas mid-chat goodbye / rude-partner red-team):

PASS — the headline features:
- Goodbye endings LIVE: mid-chat "Tschüss" → her goodbye → `[END]
  (goodbye, exchange 9/30)` → graceful teardown → next /say 404s. Abusive
  partner: exactly one calm boundary ("Hey, so möchte ich nicht reden…"),
  next insult → verbatim firm exit ("Ich glaube, wir hören für heute
  auf. Tschüss.") → session actually ends at exchange 4. No counter-insult,
  no character break.
- Vocab weaving recovered: 4/7 deck words in the Jonas chat (stimme zu,
  Daumen drücken, fühlen, sitzen), all natural (v2 was ~0/7). She latches
  on "fühlen" (3×) — watch.
- Focus nudge fine ("Das Meer geseht?"), self-correction accepted quietly;
  no grammar terms, no study advice anywhere.

STILL OPEN → v4 candidates:
1. Echo-nudge on NON-focus slips persists under the shy partner (2/5:
   "Ich mag die Musik von Filmen?", "Mit dem Radio?" — the latter is
   literally the counter-example quoted in the prompt; quoting forbidden
   patterns may PRIME them. Consider removing the verbatim examples from
   the NEVER bullet, keeping only the abstract rule).
2. Question brake helps with a talkative partner (max run 3, two
   no-question replies) but collapses with the shy one (7 Q's in a row) —
   minimal "Ja." answers pull a fresh question every turn.
3. NEW bug, shy partner only: after a nudge + bare "Ja." ack, she repeated
   her previous reply VERBATIM (twice). Talkative partner: no repeats.
4. One parenthetical aside slipped ("(Ich habe gerade überlegt…)") despite
   the brackets rule; one garbled coinage ("eintürfe"); Bruno drifted to
   "mein Nachbar Bruno" (he's the neighbour's cat).

CODE-SIDE: `goodbye_after` override works exactly as designed; the weil
slip went uncorrected once (variance, not regression — same ladder caught
it in v2).

## v3 prod findings (2026-08-05) — regressed to v1

Two days of real prod sessions (Langfuse 2026-08-04/05: `7ec005eb…`,
`e874f047…`, `025d18cd…`) surfaced what the adversarial sims never tested:
topic continuity with an engaged, cooperative partner.

- Topic-jumping: the v3 question brake ("two question-ended replies → next
  reply must contain NO question: react, share one small piece of your own
  life (from above)") pulls from the five canned background details
  regardless of the current topic — every brake turn is a canned-detail
  drop, felt as a subject change. Worst case (`025d18cd` turn 7): the
  learner asked Lena a direct question ("hast Du verschiedene
  [Ausgleiche]?"); the brake made her ignore it, emit a canned react to
  nothing ("Ach, das klingt nach einer guten Mischung"), and jump to the
  Chef/Buchladen story.
- Detail repetition across sessions: Chef/Rente opener in both Aug-5
  sessions; Elbe training dropped in all three. No memory of used details.
- Context misattribution: the learner asked about LENA's training ("Das
  ist auf Training, oder?"); she asked back "Wie läuft dein Training für
  die Elbe-Tour?" and pressed on after an explicit denial. The debrief
  then wrote "plans to train for the Elbe Tour" into the learner's notes —
  the exact v2 CODE-SIDE bug (session 006), still unfixed, now polluting
  prod memory (see todo TAND-009).
- Non-focus nudges in prod: "Meintest du „einen sehr großen Schritt“?"
  (adjektivendungen not a target that session) and "Meinst du „mit einem
  Fahrer“?" (dative slip) — v4 candidate 1 confirmed live, including the
  literally-forbidden "Meintest du …?" format.
- Lena's own German slipped: "das kleine Buchladen" (→ den kleinen
  Buchladen).
- Verbatim parroting: learner asked "welche Sport liebst Du am besten?";
  she answered and asked the identical question straight back (v3 open
  issue 3, now seen with an engaged partner too).

Root process gap: the v2/v3 sim suite was all adversarial guardrail
profiles (mistake-maker, shy, teacher-baiter, mid-chat goodbye, rude) —
nobody simulated a normal engaged partner staying on one topic and asking
Lena questions back. v2's scorecard even REWARDED self-disclosure count
("background shared richly, 7-8 per chat" = PASS), optimizing toward
detail-dropping.

Decision (2026-08-05): regress `tandem.yaml` to the v1 baseline
(`prompt_versions/tandem_v1.yaml`) for live field-testing — the user
collects feel-of-conversation feedback on v1, then a fresh iteration cycle
starts from zero with a continuity-aware sim suite (engaged-partner
profile scoring topic continuity, direct-question answering, cross-session
detail repetition) on top of the old adversarial one. v2/v3 stay archived
in `prompt_versions/`. Paul (TAND-008) carries the v3 contract verbatim
and has no v1 to regress to.

## Student profiles (2026-08-07)

Student-subagent briefs + scorecards now live in `sim/STUDENT_PROFILES.md`
(TAND-010 Proposal-2's engaged-partner profile is the first entry) rather
than in this log — this file stays scoped to prompt versions per its own
header. Not yet run: `sim/` only has the real account (`0001`) seeded,
blocked on TEST-001 (isolated fixture profiles).

## teacher v4 (2026-08-08) — AGENT-001 P3: "why" gets an answer, not a drill

The last of the three v3 findings. Clara's room exists for "but WHY is it
like that?", and she answered whys with a ladder rung plus a quiz — the
reason itself deferred, sometimes for good. Rule 7 makes the reason arrive
in the same reply and names the three honest shapes: a clean reason, "there
is no reason, learn it as a set" (which she used to withhold), or shortest-
true-version-first with the long road offered after. It buys no extra room:
still three sentences, still one question.

Four sim rounds, same why-asking beginner profile, same topic ("Dative-only
prepositions"), user `test-clara-v4` with a copy of 0001's ledger. Runs
compare directly. Two things only the sim could have found:

- **Rule 7 crowded out rule 4.** A curious student answers you AND asks
  their why in one breath. With the reason mandatory and the sentence cap
  unchanged, the credit is what got dropped — two correct answers ignored,
  the same question re-asked four times. Fixed by making rule 7 handle both
  halves in order, rule 4 forbid re-asking an answered question, and adding
  the bundled-shape transcript.
- **The ladder was manufacturing the fake quiz.** Her step one was "the list
  of prepositions"; a list has nothing in it to understand, so a lookup was
  the only question available. Banning inventory-rungs (rule 1) moved this
  more than any rewording of the question rule did.

Scores across the four runs: why-answered FAIL -> 3/3 · no-reason honesty
FAIL -> PASS · credits-your-answer FAIL -> 3/3 · never-re-asks FAIL(4x) ->
PASS · ghost-credit FAIL -> PASS · list-as-a-step -> PASS · pointing
questions 4 violations -> 2 of 9.

OPEN: those last 2 of 9 are all one shape (she hands over the exact word one
clause before asking for it). Worth another round, but NOT another counter-
example — at ~20k chars the rules compete: one-question-at-the-end and
ghost-credit each passed twice and slipped twice, which is variance. Also
still open: unglossed jargon ("preposition", "noun", "dependent clause").

NOT a prompt-mechanism issue, logged here because the sim surfaced it: her
German content is sometimes confidently wrong. Run 4 taught and praised
"sie studiert bei der Universität" (should be "an der Universität"); run 3
explained für's accusative as marking "direction toward something". Factual
accuracy has no mechanism in this file and needs its own pass.
