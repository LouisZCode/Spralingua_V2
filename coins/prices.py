"""Unit prices and allowances for PAY-002.

Exchange rate: 1000 coins = €1. All unit prices are rounded UP ~20–40% from
the measured cost table in cost_price.md (2026-08-24 entry) so N coins spent
always costs the project less than N/1000 euros — the margin table there is
built on these rounded numbers.

- SATZ_ATTEMPT (vocab card / flow item, spoken): 5 coins ≈ 0.4¢ measured →
  0.5¢ at 5 coins (+25%). A flow item is the same judge STT+LLM pattern as a
  vocab card, so one constant covers both; the Flow router reuses the same
  charge site.

- LETTER (Briefkasten full cycle, one charge at letter creation): 15 coins ≈
  1.3¢ measured → 1.5¢ (+15%). Loosely 3 vocab attempts' worth; the whole
  letter (hints + corrections + germanize) is included in one charge.

- VOICE_EXCHANGE (one learner↔agent turn in tandem/teacher/conversation/
  respond): 15 coins ≈ 1.2¢ measured → 1.5¢ (+25%). TTS dominates; Clara's
  ~2¢/turn pre-diet cost is intentionally carried at the same 15 until the
  verbosity diet lands (see cost_price.md's "Clara decision" note).

- INTERVIEW_ANSWER (one judged answer chunk): 20 coins ≈ 1.7¢ measured →
  2.0¢ (+18%). Three judges + ~90s batch STT per chunk.

Free: idiom rephrase, gloss hover, nudge suggestions, explain, add-a-word
pool links — not on the unit table, priced at 0.

DAILY_ALLOWANCE: free 0 (but a one-time 100-coin grant at signup covers day
one), basic 200, premium 500 — the hard cost ceiling per day. Unused daily
coins do NOT roll over; purchased coins (grant + top-ups) survive resets.

TOPUP_COINS: 500 per €2 — the only way to buy past today's allowance.
SIGNUP_GRANT: 100 — one-time, into the purchased bucket at row creation.
"""

# PAY-002: one constant per billable unit; every route imports these — the
# "single source of truth" contract the spec names.
SATZ_ATTEMPT = 5
LETTER = 15
VOICE_EXCHANGE = 15
INTERVIEW_ANSWER = 20

DAILY_ALLOWANCE: dict[str, int] = {
    "free": 0,
    "basic": 200,
    "premium": 500,
}

TOPUP_COINS = 500
SIGNUP_GRANT = 100
