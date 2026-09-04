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

## teacher v8.9 (2026-09-04) — AGENT-006: volunteered sentences get graded, not rubber-stamped

The other half of the test-audit-c gap AGENT-005 (v8.8) didn't reach: when
a learner volunteers a German sentence of their own — ordinary speech, not
an app ⟦ÜBUNGSERGEBNIS⟧ report — nothing told Clara to actually check it
before reacting. The existing line ("react to what is RIGHT first, then fix
at most one thing") sat low in the prompt with no grading step behind it.
AGENT-005 v2 tried strengthening it into an every-turn "judge before
anything else" constraint and was rejected: two live sims (scratchpad
agent005/runC.md, runD.md) showed it swing to the opposite failure —
wrong sentences praised ("Exactly — hänge, you heard it. Ich hänge das
Bild an der Wand.") and a genuinely correct uncontracted "Ich bin in dem
Park gewesen" false-corrected to "im Park gewesen", because the rule said
react, never check.

This round built the eval axis first (evals/teacher/dataset.jsonl,
`judge.py`'s new `volunteered-sentence` axis, 8 items: vs01-vs08 — wrong
before/during a pending exercise, correct uncontracted "in dem"/"an dem",
a turn mixing a correct sentence with a wrong follow-up, a spoken-style
item with a plausible STT-trimmed ending, and a correct dative-only
preposition that must not be miscorrected via two-way logic), then
iterated the prompt against it instead of against sims alone — the
gap Proposal-2 (sim-only) flagged: "two sims cannot tell 'reacts more'
from 'grades correctly'".

Baseline (v8.8, unmodified): 42-item baseline 34/42 = 81%; new axis 2/8 =
25% — reproducing exactly AGENT-005 v2's two failure poles plus three more
(a mixed-sentence turn graded as one blob, a correct dative-only "mit"
mis-"corrected" via two-way-preposition logic despite rule 4 already
listing it as dative-only, and an STT-trimmed ending flagged as an error).

v8.9-agent006-1: folded a same-turn grading step into rule 4 (the existing
"check a claim before confirming it" rule — a volunteered sentence is
graded the same way, not a new rule fighting rule 2's dealing instinct):
"check its case against the rule first — im = in dem, am = an dem, both
correct... then confirm briefly if it holds, or name and fix exactly the
one wrong thing if it doesn't." Two trap pairs, quoting the rejected v2
sim's actual failing lines verbatim. Removed the old "react to what is
RIGHT first" sentence (superseded, and its praise-first framing was the
risky phrasing itself). Axis: 2/8 -> 5/8. Baseline: 34/42 -> 36/42 (both
within the run-to-run noise band, no drop). Remaining fails: the
mixed-sentence turn, the STT-trim item, and a motion/accusative miss where
she identified the error but ELICITED instead of fixing it directly —
rule 3's pointing-question habit leaking into ordinary speech.

v8.9-agent006-2: reconciled the elicit leak explicitly ("directly, never a
pointing question here; that one question is rule 3's alone, for an app
verdict" + a trap pair anchored on the actual failing line, "Not quite —
you kept dem — which case does that article show?"), added STT-trim
tolerance to the grading step, and a multi-sentence clause ("more than one
sentence in the same turn is graded separately"). Axis: 5/8 -> 6/8 (elicit
leak fixed). Baseline: 36/42 (stable). Remaining: the mixed-sentence item
now over-corrected in the OTHER direction (both sentences waved through as
correct — she wasn't checking the verb per sentence, just pattern-matching
"in dem" as safe), and the STT-trim item still flagged the trimmed
ending.

v8.9-agent006-3 (SHIPPED): two more trap pairs, both quoting this round's
own failing lines verbatim per LEARNINGS.md's "trap written from real
failing output lands immediately" — one telling her to check the VERB per
sentence, not just the preposition phrase, for the mixed-sentence case; one
modeling a clean confirm-only reply for the STT-trim case. Axis: 6/8 ->
7/8, held at 7/8 (88%) across 3 repeat full 50-item runs — the specific
failing item varies (vs02 once, vs06 twice out of 3), which is the
temp>0 flip-rate LEARNINGS.md already documents, not a new instability.
Baseline held/improved across the same 3 runs: 37/42 (88%), 39/42 (93%),
38/42 (90%) — all above the original 34/42 (81%) measurement, comfortably
inside noise, no regression.

Ship criterion (stated up front): axis >= 7/8 sustained across >= 2 of 3
repeat runs (a single run at production temperature is not a measurement —
LEARNINGS.md 2026-09-01), AND the 42-item baseline does not drop below its
first-measured 81%. Both held; shipped as v8.9.

OPEN: the STT-trimmed-ending item (vs06) is the one persistent flake — 1/3
across the three v3 runs, below what a trap pair alone reliably pins. Per
LEARNINGS.md's "what didn't work" #1 (the `**den**`-read-as-"star-star-den"
precedent): two shape-pinned prompt fixes (the grading-step clause + a
dedicated trap pair) didn't fully hold, which is the model's own signature
of a sampling habit rather than a rule-comprehension gap. Not chased
further inside this round's 3-version budget — worth a code-side mitigation
(e.g. a light STT-artifact normalization before the turn reaches Clara) if
it recurs live, rather than a fourth prompt round.

Persona-block cost: 13,627 -> 15,663 chars (+2,036, or +14.9%), against
242 chars trimmed from three redundant lines (old "react to what is RIGHT
first" sentence, "Never stack explanations inside parentheses", "as Clara
throughout") — not net-zero like v8.8's round, an honest cost for a new
same-turn rule plus five trap-pair anchors (two poles fixed in round 1,
elicit-leak in round 2, mixed-sentence + STT-trim in round 3). Still well
inside the ~26k total-prompt budget. Full run reports:
evals/teacher/runs/2026-09-04-agent006-{v88-baseline,v89-1,v89-2,v89-3,v89-3b,v89-3c}.md
(gitignored, local only). Prompt snapshots:
sim/prompt_versions/teacher.v8.9-agent006-{1,2,3}.yaml.

### AMENDMENT (2026-09-04, same day) — an independent live-sim tester FAILED the above, harness fix, round 2

The v8.9 shipped above (round 1-3, axis 7/8 stable) was NOT actually safe
to ship: an independent tester ran live sims and found that in 3 of 4
live trials, a wrong volunteered sentence got rule 3's ELICIT (a pointing
question) instead of rule 4's direct fix — e.g. "Ich gehe in dem Park." ->
"Not quite — you kept in dem here. What case does the preposition need
when you are moving into the park?", a near-verbatim reproduction of the
prompt's own labelled WRONG trap. Both AGENT-005 poles (wrong-praised,
right-false-corrected) held live; this was a third, undetected failure
mode. Transcripts: scratchpad agent006-test/ (this agent's own scratch).

**Why the eval missed it — two harness gaps, both real, both fixed:**

1. `evals/teacher/prompt_build.py` hand-reimplemented
   `conversational_prompt.py`'s teacher branch and silently omitted two
   blocks production ALWAYS renders: the CLARA-15 fallback addendum and
   the CLARA-16 exercise-catalog block — the latter alone prints dozens
   more "Typical slip: wrong -> correct" pairs pulled from the taxonomy for
   every OTHER pattern in the pool, not just the 3 focus ones. The eval was
   grading a materially shorter, less representative prompt than gpt-oss-
   120b actually sees.
2. All 8 vs01-vs08 items defaulted `focus_present: false` — a prompt shape
   a real connect almost never reaches, since `pipeline/factory.py` falls
   back to seeded starters rather than ever leaving `grammar_focus` truly
   empty.

**Fix 1 (harness):** `prompt_build.py` now calls
`agents.conversational_prompt.layered_prompt_middleware` DIRECTLY instead
of re-implementing it — feeding its compiled `AgentMiddleware`'s
`wrap_model_call` hook an identity handler hands back the fully-overridden
`ModelRequest` (system prompt included) instead of driving a real model
call, so any future drift in the production assembly is caught by
construction, not by a second copy falling out of sync. `exercise_catalog`
is now built with the SAME `teacher.registry.coverage()` +
`grammar.loader.load_taxonomy()` calls `pipeline/factory.py` makes.

**Fix 2 (dataset):** added `focus_present: true` twins of all 8 vs items
(`vs01-fp` … `vs08-fp`) — 16 volunteered-sentence items total, dataset now
58 items (was 50). Full assembled prompt for a focus-present item: ~26-28k
chars depending on version (was never measured before — the old harness's
persona-only view made "~26k total budget" impossible to check against
reality).

**Corrected-harness re-baseline (same v8.8 / same "v8.9" as above, just
through the fixed harness):**

| version | 42-item baseline | 16-item axis |
|---|---|---|
| v8.8 | 31/42 = 73.8% (2 identical runs) | 8/16 = 50.0% |
| "v8.9" (rounds 1-3 above) | 31/42 = 73.8% (2 identical runs) | 11-12/16 = 68.8-75.0% |

The baseline dropping from the old harness's 81-93% to a stable 31/42 is a
HARNESS effect (confirmed: v8.8 scores identically, 31/42, both times) —
the fuller, more realistic prompt is simply harder on unrelated axes for
every version equally, not a regression from any rule-4 edit. The true
floor to hold against is this 31/42, not the old harness's 34/42, though
this round's shipped version clears both anyway (see below).

**Round 1 (SHIPPED as v8.9's final content):** rule 1's exception now
names the ⟦ÜBUNGSERGEBNIS⟧ sentinel explicitly ("after a wrong
⟦ÜBUNGSERGEBNIS⟧ verdict specifically — never any other kind of 'wrong'");
rule 4 restructured to lead with "never a pointing question here" BEFORE
the case-check (primacy — the direct-fix instruction was previously
buried after the check, competing for attention against rule 3's much
more heavily-modeled elicit shape); 3 more trap pairs, all quoting this
round's own live/eval failures verbatim (a location-dative elicit on
"Ich bin gestern in den Supermarkt gewesen", the same elicit leak inside
a mixed-sentence turn, and the STT-trim item's WRONG line upgraded to its
newer, elicit-shaped failure). Eval: 42-item baseline 33-36/42 (3 runs),
16-item axis 14/16 = 87.5% stable across all 3 runs (evals/teacher/runs/
2026-09-04-agent006-v89-4{,b,c}.md). Persona 13,627 -> 16,601 chars
(+2,974 net); full assembled prompt ~26.8k.

Live battery (own :8772 backend, `test-agent006` fixture, `role=developer`,
`--copy-from 0001 --copy-cards`, `sim_isolated.py` copied into this
agent's own scratch dir with `SCRATCH`/`BASE` repointed to avoid the
tester's now-stale :8774 state file): 5 sentences per run — "Ich gehe in
dem Park." (wrong), "Ich lege das Buch auf dem Tisch." (wrong), "Ich bin
letztes Jahr in dem Park gewesen." (right), "Ich habe das Bild an die Wand
gehängt." (right), "Ich hänge das Bild an der Wand." (wrong, the original
test-audit-c sentence) — three separate live sessions, backend restarted
fresh each time:

- Run 1: 3/5 (misses: the gewesen sentence false-corrected as "movement",
  and the final Wand sentence got dropped entirely for "Here's a real one
  on two-way prepositions" — a return of the ORIGINAL pre-AGENT-005 bug,
  after four consecutive unanswered "want a real exercise?" invitations).
- Run 2: 5/5 clean.
- Run 3: 5/5 clean.

Neither run-1 miss recurred in runs 2 or 3 — both read as isolated
temp>0 noise, not a systematic defect, consistent with LEARNINGS.md's
established single-sample-is-noise finding extended to live sessions.

**Round 2 (tried, NOT shipped — `sim/prompt_versions/teacher.v8.9-agent006-5.yaml`):**
targeted run 1's two specific misses directly: a 4th same-turn clause in
rule 4 ("this runs EVERY time, no exceptions... dealing is a separate,
later decision that never substitutes for reacting") plus an explicit
"a verb of BEING somewhere is a state, not motion" anchor distinguishing
`gewesen`/`ist geblieben` from `ist gegangen`/`ist gefahren`, plus 2 more
trap pairs quoting run 1's exact failing lines. Eval: axis 13-15/16 across
3 runs (avg 87.5%, noisier than round 1's flat 14/16/14/16/14/16), baseline
34-38/42 (slightly better than round 1's). Persona 18,004 chars; full
assembled prompt ~28.2k — the largest yet.

Its OWN live battery (fresh backend restart, same 5 sentences): **2/5** —
worse than round 1. It DID fix both of round 1's specific misses (the
gewesen sentence confirmed correctly, no drop-to-deal on the Wand
sentence) but the elicit leak came back on 3 of the 5 sentences, including
one where she also hallucinated an unrelated example sentence
("Ich gehe in das Haus.") before the elicit. Reading the two rounds
together: round 2 fixed what it targeted but made the thing round 1 had
already fixed WORSE, on a prompt ~1,400 chars longer. The likely
mechanism — not proven, but consistent with everything else in this round
— is that rule 4's instruction, sitting early in a now much longer
persona block with proportionally more content after it (the catalog
alone can run into the tens of thousands of characters), loses salience
by the time generation reaches the actual reply; adding more anchor text
made the prompt longer without moving rule 4 closer to the point of use,
so the net effect on THIS specific defect was negative. Not something
LEARNINGS.md names yet — logged here as a candidate addition ("prompt
growth can un-fix an already-fixed defect once total length crosses some
threshold — verify a load-bearing fix survives on the FULL assembled
prompt, not just in isolation, before trusting it").

**Decision:** shipped round 1's content as v8.9 (persona 16,601 chars,
full prompt ~26.8k). Round 2's numbers were marginally better on the eval
axis and noticeably better on the corrected-harness baseline, but its live
battery was worse on the exact defect this whole task exists to fix, and
the task's ship criterion is the live battery, not the eval alone — round
2 was rejected on that basis despite the (misleading, in hindsight) eval
axis numbers looking fine. Round 2's file is kept at
`sim/prompt_versions/teacher.v8.9-agent006-5.yaml` for the record, not
loaded live.

Full run reports:
evals/teacher/runs/2026-09-04-agent006-{v88-fullharness,v89-fullharness,v89-fullharness-b,v89-4,v89-4b,v89-4c,v89-5,v89-5b,v89-5c}.md
(gitignored, local only). Prompt snapshots:
sim/prompt_versions/teacher.v8.9-agent006-{4,5}.yaml (4 = shipped).

## 2026-09-05 — TAND-013 opener verification (no prompt change)

Pure measurement, tandem prompt v5.3 (`63b9124`) unchanged — verifying the
2026-08-15 trace-review finding (Lena opened 9/10 replies with "Ach, …" in
one real session) is actually fixed by v5.3's anti-repeat / shape-not-script
rules (`agents/prompts/tandem.yaml` ~144-158), per that entry's own
"verify it in the next real chat" follow-up. Harness: own backend on
`:8793` (`SPRALINGUA_TEST_GUARD=1`), `:8765`/`:3000` untouched throughout
(confirmed same PID before/after). Fixtures: `test-t013-nina` (`--profile
plateaued`) for session 1 (Lena, `tandem`); a same-shaped second fixture
`test-t013-paul` for session 2 (Paul, `tandem_paul`) — the plateaued
fixture's starting balance (175 coins: 100 signup grant + 75 daily
allowance) covers exactly one 10-exchange voice session at 150 coins, not
two back-to-back, and no in-scope tool could top up `purchased_coins`
without tripping the environment's write-classifier (even a read-only
balance check was blocked) — using a second identically-seeded fixture
sidesteps that without editing any file, and doesn't affect what's being
measured since Paul's notes are already keyed independently by `lesson_id`
(`load_tandem_notes`), same as if the same user id had been reused. Both
sessions played the Nina persona brief verbatim (topic "Arbeit", blocking
fact "Leider habe ich gerade keinen Job" landed turn 2, ≥3 direct
questions with answers), `--exchanges 10`, ended at `SESSION_ENDING
(max_exchanges, exchange 10/10)` both times.

**Session 1 — Lena (`tandem`):**

| # | first word | note |
|---|---|---|
| 1 | Ich | — |
| 2 | Das | — |
| 3 | Ja | corrected "vor drei Monate" → "vor drei Monaten" |
| 4 | Ich | — |
| 5 | Ich | — |
| 6 | Ich | — |
| 7 | Ja | — |
| 8 | Ich | — |
| 9 | Ich | goodbye-flavored ("wir sprechen bald wieder") but did not arm SESSION_ENDING |
| 10 | Das | cap hit; spoken goodbye ("Bis bald, mach's gut!") |

0/10 "Ach" (0%). Longest identical-opener run: **3** ("Ich" at #4-#6).
Distinct openers: 3 (Ich/Das/Ja).

**Session 2 — Paul (`tandem_paul`):**

| # | first word | note |
|---|---|---|
| 1 | Bei | — |
| 2 | Ich | — |
| 3 | Wir | — |
| 4 | Ich | — |
| 5 | Stand | — |
| 6 | Stand | — |
| 7 | Ja | — |
| 8 | Bei | "das klingt sportlich" — 3rd near-verbatim use of "sportlich" across #3/#4/#8 (not scored, noted) |
| 9 | Stand | — |
| 10 | Freut | cap hit; spoken goodbye ("Tschüss, bis bald.") |

0/10 "Ach" (0%). Longest identical-opener run: 2 ("Stand" at #5-#6).
Distinct openers: 6 (Bei/Ich/Wir/Stand/Ja/Freut).

**Side observations (both sessions):** no re-ask of anything already
answered at length (the original Nina failure mode) in either transcript;
zero parentheses/brackets in any spoken PARTNER line (`grep -c "("` = 0 on
both transcripts); both goodbyes arrived as a real spoken sentence exactly
at the exchange cap, not a stage-direction leak — though Lena's #9 also
read as a soft goodbye one exchange early without tripping detection,
which is a near-miss worth knowing about even though it isn't this
entry's subject.

**Verdict: FAIL.** The literal "Ach" tic is gone (0% in both sessions,
well under the 30% bar), so v5.3 did fix the specific symptom TAND-013
named. But the PASS rule also requires no first-word run ≥3 in either
session, and Lena's session hits exactly that with three straight "Ich"
opens (#4-#6) — the anti-repeat rule suppressed the one word it names as
an example ("Ach") without stopping opener-repetition as a class. Paul's
session passes both conditions cleanly. This supports the lead's
Proposal-2 (a code backstop counting consecutive identical first words in
`ClientWrapper.astream`) rather than trusting the prompt-only fix as
sufficient — the prompt rule caught the letter of the 2026-08-15 finding,
not the underlying pattern.

Transcripts and backend log kept locally (not committed): scratchpad
`tand013/{session1_lena_transcript.txt,session2_paul_transcript.txt,t013-backend.log}`.

**Lead's reading and decision (same day, after an independent re-score
of both transcripts that matched every number above):** the "Ach," tic is
gone and the two v5.3 fixes hold, so TAND-013 stays Solved. The three
straight "Ich …" opens are not the failure the entry described: v5.3's
default reply shape asks for "first ONE sentence of your own about the
exact detail they just gave", and the natural German subject of a
self-disclosure clause is "Ich" — three consecutive learner turns inviting
one (course value, job satisfaction, an interview) plausibly get three
"Ich"-led sentences from a careful human too. "Ach" was filler,
interchangeable and meaningless; "Ich" is load-bearing grammar. So
Proposal-2's consecutive-first-word nudge is deliberately NOT built: a
counter that trips on the commonest German sentence opener would push the
model away from the very shape the prompt asks for. What the run did find
is Paul's, and it is a prompt matter for the next Paul round, not a code
one: "Stand jetzt" four times in ten replies (three as the opener), "das
klingt/ist sportlich" three times, and the same "[X] würdest du gern …
[verb]" question shell at #3 and #8 — the TAND-010 "Elbe" class (an
example phrase copied as content), recorded on PRODUCT-004. Lena's #9
soft goodbye one exchange early, without arming detection, is noted
there as well.
