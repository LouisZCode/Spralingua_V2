"""Grammar-pattern taxonomy for the error ledger (GRAM-001).

The taxonomy (``taxonomy.yaml``) is the fixed catalog of German error
patterns every extractor classifies into — free-form error labels would make
the ledger noise. Content-as-data, like the lesson YAMLs and satz packs.

Kept as its own light package rather than inside ``agents/`` on purpose:
``satz`` (the Phase-1 harvester) needs the catalog too, and importing
anything under ``agents.*`` executes ``agents/__init__.py``, which drags in
the whole Pipecat/LangChain conversation stack.
"""

from .contractions import expand_contractions
from .levels import (
    BUCKETS,
    DEFAULT_BUCKET,
    allows,
    bucket_of,
    level_for_pattern,
    select,
)
from .loader import load_taxonomy, taxonomy_brief

__all__ = [
    "BUCKETS",
    "DEFAULT_BUCKET",
    "allows",
    "bucket_of",
    "expand_contractions",
    "level_for_pattern",
    "load_taxonomy",
    "select",
    "taxonomy_brief",
]
