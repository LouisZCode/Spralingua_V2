"""Verbformen — the verb principal-parts drill (GRAM-002, Exercise C).

Past-tense misses are LEXICON, not rule ("every miss was a strong verb
wearing a weak ending"), so the prescription is flashcarding — and the
Satzschmiede spoken-past sibling cards ARE those flashcards. The deck
auto-feeds from the learner's Satzschmiede pool; scheduling and removals are
drill-local on the ``user_verbformen`` overlay, so this mode never moves the
shared Satzschmiede schedule. Judging reuses the satz examiner wholesale
(Perfekt OR Präteritum both pass); slips feed the grammar-error ledger.
"""

from .routes import router

__all__ = ["router"]
