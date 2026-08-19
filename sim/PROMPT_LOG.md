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

## tandem v4 (2026-08-11) — three v1 field findings, each given a mechanism

Two weeks of v1 field-testing (see "v3 prod findings" above for why v1 was
brought back) surfaced three problems, distinct from anything the v2/v3 sim
rounds caught:

1. **Topic-jumping.** She hops from topic to topic instead of staying on the
   chosen one and going deeper. Mechanism: "How you talk" now makes today's
   topic the only one SHE ever raises — she digs into the partner's exact
   detail, and only the partner may change the subject; when they do, she
   follows them fully, never back. Added a labelled jump counter-example
   ("Und was isst du sonst gern zum Frühstück?" out of nowhere) plus a good
   follow-up example (same answer, dug into the detail they gave).
   `short_term_template`'s "Let it wander from there" — which invited the
   drift — is now stay-and-go-deeper phrasing.
2. **Vocab cramming.** `vocab_header`'s "Work EVERY one of them in" made
   word-salad — exactly the un-natural teacher behaviour the persona bans.
   Mechanism: words are opportunities, not a checklist — used only when the
   conversation genuinely passes through their sense, inside a sentence she'd
   say anyway; unused is fine; never bend the conversation toward a word,
   never stack two in one sentence.
3. **Too much talking.** Reply length tightened from "1 to 3 sentences" to
   1-2 (3 only rarely, sharing something of her own the partner asked for).
   Default reply shape named explicitly: brief reaction + ONE question
   digging into what they just said. Added a labelled 4-sentence
   counter-example (reflects, shares two things of her own, generic
   question — "ignores their detail").

Also removed the prompt's own hardcoded "after about 12-14 exchanges"
wrap-up line — the backend now injects a wrap-up cue carrying the session's
real exchange cap, so the prompt can't carry a copy that drifts from it
(code-side, not this file).

v1-live snapshotted verbatim at
`sim/prompt_versions/tandem_v1_live_2026-08-11.yaml` before replacement.

Sim verdict (2026-08-11, Sonnet student, seeded fixture user `test-lenav4`,
two sessions — 8 exchanges on "Kochen und Essen" at the default cap, 5 on
"Musik" via `--exchanges 5`):

- One-topic discipline: PASS. All 13 replies across both sessions stayed on
  the chosen topic; every question dug into the partner's just-given detail
  (soup → roommate's rice → vegetables → bread → oven → family cooking).
  No unprompted pivot anywhere.
- Vocab: PASS. Session A's 7 deck words all went unused (permitted); session
  B used "schön" 3x as a reaction opener but never bent a sentence toward a
  word and never stacked two. No cramming.
- Reply length: mostly PASS — 11 of 13 replies at 2 sentences, two at 3 (one
  a grammar nudge, one a reaction+question that ran long). No 4+.
- Correction contract intact: focus-pattern nudge fired on "verbrennt" (and
  on "mit meine Schwester"); a non-focus slip ("von die Welt") got the
  silent echo, no comment.
- Cap override end-to-end: PASS — `--exchanges 5` logged `vocab_words=4`
  (scaled sample) and closed at `SESSION_ENDING (max_exchanges, 5/5)`.

Open for v5: she occasionally re-asks a fact the partner already answered
(once per session — "Bei wem bekommst du den Rat?" right after "meine
Mutter"; "warst du mit deiner Schwester dort?" two turns after the answer).
A question-is-finished-forever rule, teacher.yaml rule-4 style, is the
likely mechanism if a future round confirms it.

## v5 (2026-08-19) — contribution-first: the interrogation fix

Field report (user, 2026-08-19): v4 sessions feel like an interview —
reaction + question every turn, nothing of Lena in them. v4's own default
shape mandated exactly that; its too-much-talking counter-example punished
self-disclosure outright.

Design (both partners — tandem_paul gets its first calibrated pass,
including the v4 one-topic anchor its short_term_template never received;
it still said "Let it wander from there"):
1. Contribution-first default shape: ONE own sentence (opinion / small
   experience / gentle disagreement) pinned to the partner's just-given
   detail, THEN at most one follow-up question. 2 sentences home, 3 ceiling.
2. Question valve: after two question-ending replies, the next ends on the
   contribution. Statement turns are legal and demonstrated (GOOD example).
3. Answered-question rule: a question once answered is finished forever
   (the open v4 finding — she re-asked ~1 answered fact per session).
4. Shape-not-script note closing the German example lines (v1's "Elbe"
   example became TAND-010's canned repeated detail — examples teach
   shape, models copy content).

Research grounding (Sonnet researcher memo, 2026-08-19; confidence tags
per item): Hardy, Paranjape & Manning, SIGDIAL 2021 [verified] —
back-channeling, personal disclosure, and statements-instead-of-questions
each significantly raise user initiative in social chatbots,
statement/back-channel turns strongest. Moon 2000, J. Consumer Research
[verified abstract] — machine self-disclosure elicits reciprocal user
disclosure. Huang et al. 2017, JPSP [recalled] — follow-up questions
specifically raise liking, so follow-ups stay while new-topic probes stay
banned. Long & Sato 1983 [recalled] — referential beats display questions
for learner output length. Lyster & Ranta 1997 [recalled] — elicitation
beats recasts for uptake; independently supports the unchanged nudge-first
correction contract. Li et al. 2024, arXiv:2402.10962 [verified abstract]
— persona/topic adherence decays measurably within ~8 turns; logged as a
possible future code-side mechanism (mid-session re-anchor), not addressed
in v5.

Live predecessors snapshotted: tandem_v4_live_2026-08-19.yaml,
tandem_paul_live_2026-08-19.yaml.

Sim verdict (2026-08-19): pending — round below.
Sim verdict (2026-08-19, round 1 — Nina, engaged, 10 exchanges, "Kochen
und Essen", fixture test-lenav5):

- Contribution rate 9/10 (target ≥70%): PASS — the headline v5 mechanism
  works; v4's reaction-only turns are gone. Two of the nine were weak
  (one mirrored the learner's own sister-detail back, two leaned on the
  same Buchladen detail back-to-back).
- Question-density 9/10 = 90% (target 60–85%): FAIL — the valve fired only
  on the final reply. Session still reads interview-ish in cadence even
  though every question was an on-detail follow-up.
- Jumps 0, blocking fact (kein Ofen) respected across all 10, prompt-
  example verbatim reuse 0, length all ≤3: PASS.
- Re-asks: 1 soft FAIL — "mit wem gegessen?" answered "allein", two turns
  later "mit wem würdest du teilen?" (same fact wearing würde).
- Reciprocity: reply 3 DODGED the learner's direct "Und du? Was kochst du
  gern?" — mirrored her detail and asked onward instead of answering.
- Corrections (3 seeded slips): case nudge clean PASS; gender absorbed
  naturally PASS; word-order nudge BROKEN — echoed the learner's broken
  order back with a comma ("Gestern, ich habe das Dressing gemacht?").
- Model-side noise, not prompt-addressable: one own-grammar slip by Lena
  ("finden ich"); no farewell at the 10/10 hard cap (same behavior as the
  v4 5/5 run — logged, not treated as a v5 regression).

v5.1 (same day): reciprocity rule (answer their question first, one
sentence, before anything else); valve hardened ("about every third reply
shouldn't end with a question" + streak counter-example); answered-forever
extended to hypothetical re-phrasings; contribution anti-mirror +
anti-repeat-of-own-detail; correction nudge for word-order slips must
carry the corrected order. Round 2 below.

Sim verdict (2026-08-19, round 2 — Nina, "Wochenende und Freizeit", 10
exchanges, v5.1): contribution 9/10; question-density 8/10 = 80% with two
genuine statement-only replies — the interview cadence is gone (round 1:
90%, valve fired once). Repeated-own-detail 0 (was 2). All three seeded
slips (verb-second, dative-prep, accusative) got nudge-first corrections
carrying the CORRECTED form — the round-1 broken echo did not recur and
the mechanism generalized to case errors. Blocking fact (kein Auto) held.
Two remainders: reply 5 answered the learner's question only at the END of
her turn (ordering, no longer a dodge); reply 8 re-asked the settled
"allein" fact in hypothetical form once, despite the würde example.

v5.2 (same day): settled-facts guard — a stated life-fact is settled,
never un-settle it with "who would you / what if you had" questions;
answer-first tightened to "FIRST thing in your reply". Round 3 = Mia
(shy regression) + Paul (engaged, office topic) below.

Sim verdict (2026-08-19, round 3a — Mia, shy minimal-answerer, "Musik", 5
exchanges, v5.2): the shy-partner regression is largely safe — all replies
≤3 sentences (no monologuing to fill silence), contributions present
without ballooning, one genuine statement-only reply, warmth held through
two "weiß nicht" dodges with zero pressure, the "der Radio" slip recast
cleanly mid-sentence. One FAIL: the final (cap) reply stacked a
confirmation tag onto a topic question ("Du hörst das Radio, richtig? …
Welchen Song…?") — the one interrogation-shape recurrence of the round.
Also noted (accepted): patient re-approach of the same unanswered
song/station question across three replies — no escalation, so allowed.
Fix queued for the round-close tidy: "a confirmation tag (richtig?/oder?)
counts as a question" appended to the ask-one-thing rule.

Sim verdict (2026-08-19, round 3b — Paul, engaged "Nino", "Arbeit und
Projekte", 5 exchanges, v5.2 — Paul's first calibrated round ever):
corrections PASS (both slips nudged with the corrected form — verb-second
and dative-prep), density 80% with one statement-only reply, blocking fact
(fully remote, no office) held, all replies ≤3 sentences. FAILs:
contribution 2-3/5 (two replies were platitudes), topic jumps ×2 (coffee,
Ventilator — the small-talk pivot, and both pivots were also his weakest
contributions), question stacks ×2 (a question-marked correction echo plus
a content question in the same reply), one invented idiom ("ein echtes
Schnäppchen an Zeitersparnissen"). Read: he lacks Lena's counter-example
battery — rules without worked examples, the exact v4 lesson.

v5.3 (same day): both files — confirmation tags count as questions (Mia
round); a correction-nudge IS the reply's one question (Paul round). Paul
only — the jump counter-example rendered in office content, and a
no-invented-idioms line in the flavor paragraph. Lena is green across
rounds 1-3 with only definitional tightenings pending no rerun; Paul rerun
below.

Sim verdict (2026-08-19, round 4 — Paul rerun on v5.3, engaged "Nino",
software-migration week, 5 exchanges): ALL FOUR v5.2 failures FIXED —
contribution 5/5 (every reply carried a pinned office detail, including
both correction turns), topic jumps 0, question stacks 0 (both correction
echoes were their reply's only question, tag included), invented idioms 0,
one clean statement-only closer, blocking fact respected, both slips
nudged with the corrected form. Style note for a future round: "sitzen
stabil" / "das Ergebnis wird schön" repeated ~3× — a naturalness tic, not
a violation.

ROUND CLOSED (2026-08-19): v5.3 is the shipping version for both partners.
Lena: green in rounds 1-3 (round-1 interview cadence and round-2
hypothetical re-ask both fixed and re-verified; Mia shy-regression safe).
Paul: green in round 4. Question-density landed at 80% engaged / with
statement-only turns present; contribution 90-100%; correction contract
intact and now carrying corrected forms on word-order slips. Known
non-blocking observations: no farewell line when the hard cap lands
mid-flow (pre-existing, also in v4); persona-drift re-anchor (Li et al.
2024) noted as a possible future code-side mechanism.
