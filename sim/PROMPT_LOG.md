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
