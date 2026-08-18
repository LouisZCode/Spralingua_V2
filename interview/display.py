"""Display + classification helpers ported from ``interview_local/app.py``
(INTV-003 slice 2: ``_company_display``, ``_interviewer_display``,
``_classify_kind``).

The workbench derives the company/interviewer labels from chunks.json's own
``"interviewer"`` field (when present) and a recording dir's exact name.
Postgres stores neither chunks.json's raw blob nor an "interviewer" column
(see ``database/orm.py::AudioItem`` — INTV-003 slice 1), only
``storage_prefix`` (``"interviews/<original dir name>"``, unchanged from the
workbench dir name) and ``title`` (the same dir name with its trailing
``"_YYYY-MM-DD_HHMMSS"`` stamp stripped, written by
``scripts/import_interviews.py::_title_from_dir``). Both helpers below are
therefore keyed off the recovered dir name (``storage_prefix`` minus its
``"interviews/"`` prefix) rather than off chunks.json content —
``interviewer_display``'s docstring explains the one behavioral gap that
leaves.

``_resolve_kind``'s ``tags.json`` override layer is deliberately NOT ported
— the workbench has exactly one manual override across its whole corpus
(Intersport's ``chunk_001`` → ``"monologue"``), and ``classify_kind`` below
already resolves it to ``"monologue"`` unaided (verified against the
imported row: duration_s=96.9, text doesn't end in "?", first word "Also"
isn't a question word — see the slice-2 report). Building an override store
for a heuristic gap that doesn't currently exist would be scope creep.
"""

import re
import string
from typing import Optional

VALID_KINDS = {"question", "statement", "monologue"}

# Verbatim from interview_local/app.py::QUESTION_WORDS — German
# question-leading words, matched only against a chunk's FIRST word,
# case-insensitively.
QUESTION_WORDS = {
    "was", "wie", "wer", "wo", "wann", "warum", "wieso", "welche",
    "welcher", "welches", "hast", "bist", "kannst", "magst",
    "möchtest", "willst", "gibt",
}

# Verbatim from interview_local/app.py::COMPANY_BY_DIR — keyed on the exact
# workbench recording-dir name, which is exactly what `storage_prefix`
# carries after "interviews/" (scripts/import_interviews.py writes
# `storage_prefix = f"interviews/{dir_name}"` unchanged from the dir name).
COMPANY_BY_DIR = {
    "AMDT-2ncdInterview_2026-07-01_105449": "AMDT",
    "Aivisory-Interview2-Paul_2026-07-28_145912": "Aivisory",
    "aivisory-Interview-Irene-Ternes_2026-07-22_151411": "Aivisory",
    "Intersport_interview_2026-05-13_095933": "Intersport",
    "SopraSteria-Interview_1_AI_Engineer_2026-07-20_105907": "Sopra Steria",
    "NiCE-CaseStudyCall_2026-07-14_155850": "NiCE",
    "OMQ-SolutionsEngineer-Interview_2026-07-31_162929": "OMQ",
    "FairDigital-Interview_1_DataScientist_2026-07-20_145920": "FairDigital",
    "Elektrovorteil - Interview 1_2026-08-11_125844": "Elektrovorteil",
}


# The trailing "_YYYY-MM-DD_HHMMSS" stamp every workbench dir name carries
# (same pattern scripts/import_interviews.py strips for the title).
_TIMESTAMP_SUFFIX_RE = re.compile(r"_\d{4}-\d{2}-\d{2}_\d{6}$")

# "Interview", "interview", "Interviews", "Interview2", … as a whole token —
# the marker separating company-ish tokens from interviewer-ish ones.
_INTERVIEW_MARKER_RE = re.compile(r"^interviews?\d*$", re.IGNORECASE)


def dir_name_from_storage_prefix(storage_prefix: str) -> str:
    """Recover the workbench's original recording-dir name from
    ``AudioItem.storage_prefix`` (``"interviews/<dir_name>"``)."""
    prefix = "interviews/"
    if storage_prefix.startswith(prefix):
        return storage_prefix[len(prefix):]
    return storage_prefix


def company_display(dir_name: str) -> str:
    """Verbatim port of ``interview_local/app.py::_company_display``.
    ``COMPANY_BY_DIR`` wins; otherwise the first token of the dir name
    (split on "-", "_" or space), title-cased, so a future interview dir
    never renders blank."""
    mapped = COMPANY_BY_DIR.get(dir_name)
    if mapped:
        return mapped

    first_token = re.split(r"[-_ ]+", dir_name.strip())[0]
    return first_token.title() if first_token else dir_name


def interviewer_display(dir_name: str) -> str:
    """Interviewer label parsed from the dir name, or "" when the name
    doesn't yield one — the frontend hides an empty interviewer line.

    The workbench prefers chunks.json's own "interviewer" field (a real
    name list) and only falls back to parsing the dir name when that field
    is missing/empty. Postgres carries no "interviewer" field at all (see
    the module docstring), so every call here parses the dir name — a
    hardened version of the workbench fallback, since nothing papers over
    its rough edges here (see the inline comment).
    """
    # Deliberate deviation from the workbench's `or dir_name` fallback
    # (which chunks.json's real "interviewer" field papered over there,
    # and nothing papers over here): tokenize the timestamp-stripped dir
    # name, take what follows an "Interview"/"Interview2"-style marker,
    # and suppress anything that still isn't a person — contains a digit
    # ("1 AI Engineer", "2ncdInterview") or just echoes the company
    # ("Intersport", "NiCE CaseStudyCall"). "" means unknown and the
    # frontend hides the line; the company label carries the card alone.
    stem = _TIMESTAMP_SUFFIX_RE.sub("", dir_name.strip())
    tokens = [t for t in re.split(r"[-_ ]+", stem) if t]
    marker_idx = None
    for i, t in enumerate(tokens):
        if _INTERVIEW_MARKER_RE.match(t):
            marker_idx = i
    if marker_idx is not None:
        tokens = tokens[marker_idx + 1:]
    result = " ".join(tokens)
    if not result or any(ch.isdigit() for ch in result):
        return ""
    if company_display(dir_name).lower() in (t.lower() for t in tokens):
        return ""
    return result


def classify_kind(text: Optional[str], duration_s) -> str:
    """Verbatim port of ``interview_local/app.py::_classify_kind``.

    Resolution order:
      1. Trimmed text ends with "?" -> question.
      2. First word (punctuation-stripped, lowercased) is a German
         question-word AND duration_s < 30 -> question.
      3. duration_s >= 45 -> monologue.
      4. Otherwise -> statement.
    """
    t = (text or "").strip()
    if t.endswith("?"):
        return "question"

    words = t.split()
    if words:
        first_word = words[0].strip(string.punctuation).lower()
        if (
            first_word in QUESTION_WORDS
            and isinstance(duration_s, (int, float))
            and duration_s < 30
        ):
            return "question"

    if isinstance(duration_s, (int, float)) and duration_s >= 45:
        return "monologue"

    return "statement"
