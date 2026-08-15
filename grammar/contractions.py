"""Preposition + article contractions (BUG-011).

German fuses a preposition with a following definite article: ``bei dem`` →
``beim``, ``zu der`` → ``zur``, ``in das`` → ``ins``. Both spellings are the
same grammar; several are the only form a native speaker would ever write
(``am Montag``, never ``an dem Montag``).

The drills stored one spelling as the item's ``answer`` and compared it
literally, so a learner typing the OTHER spelling was marked wrong. Observed
in production 2026-08-14: item "Ich helfe ___ Umzug." carried the answer
``bei dem`` and the judge rejected ``beim`` with "Used contraction 'beim'
instead of required 'bei dem'" — i.e. it rejected the more idiomatic German
of the two.

``expand_contractions`` rewrites every contraction to its two-word form so
the two spellings compare equal. Expansion (rather than contraction) is the safe
direction: it is total and unambiguous, whereas contracting would have to
guess whether a given ``in dem`` is the fusable kind.

**The case distinction survives expansion**, which is what makes this safe
for the case drills: ``im`` → ``in dem`` (dative) and ``ins`` → ``in das``
(accusative) stay as different as they were. A learner who picks the wrong
case still gets a red, exactly as before — see the ``wechselpraepositionen``
warning in ``faelle/routes.py::_matches``.

Lives in ``grammar/`` rather than in one drill because it is a fact about
German, not a drill mechanic, and three drills need it. Same reason the
taxonomy lives here: importing anything under ``agents.*`` would drag in the
whole Pipecat/LangChain stack.
"""

import re

__all__ = ["CONTRACTIONS", "expand_contractions"]

# Contraction -> (preposition, article). Only the standard-written fusions;
# colloquial-only forms that never appear in writing (``aufm``, ``vonner``)
# are deliberately absent — accepting those would teach the wrong register.
CONTRACTIONS: dict[str, tuple[str, str]] = {
    "am": ("an", "dem"),
    "ans": ("an", "das"),
    "aufs": ("auf", "das"),
    "beim": ("bei", "dem"),
    "durchs": ("durch", "das"),
    "fürs": ("für", "das"),
    "hinterm": ("hinter", "dem"),
    "hinters": ("hinter", "das"),
    "im": ("in", "dem"),
    "ins": ("in", "das"),
    "überm": ("über", "dem"),
    "übers": ("über", "das"),
    "ums": ("um", "das"),
    "unterm": ("unter", "dem"),
    "unters": ("unter", "das"),
    "vom": ("von", "dem"),
    "vorm": ("vor", "dem"),
    "vors": ("vor", "das"),
    "zum": ("zu", "dem"),
    "zur": ("zu", "der"),
}

# Whole words only. Without the boundaries "im" would eat the "im" inside
# "Zimmer" and produce "Zin demmer"; \w in Python's re is unicode-aware, so
# umlauts and ß count as word characters and "beim" won't fire inside a
# longer token either.
_RE = re.compile(
    r"(?<!\w)(" + "|".join(sorted(CONTRACTIONS, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)


def expand_contractions(text: str) -> str:
    """Rewrite every contraction to its two-word form.

    Comparison-only: the result is never shown to a learner, so it does not
    matter that ``am Montag`` expands to the unidiomatic ``an dem Montag``
    — the expected answer expands the same way and the two still match.
    Case-insensitive on input; the replacement is always lowercase, which is
    harmless because every caller lowercases before comparing.
    """
    return _RE.sub(lambda m: " ".join(CONTRACTIONS[m.group(1).lower()]), text)
