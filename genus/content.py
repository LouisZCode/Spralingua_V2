"""Content loaders + the ending classifier for Artikel-Anker (noun gender).

Same fail-loud philosophy as ``zeitfaerbung/content.py``: malformed content
aborts startup (``main.py`` lifespan) instead of 500ing mid-practice. On top
of field validation, every curated item is cross-checked against the
auto-classifier below, so a mistagged rule/trap can't ship.

The classifier is pure string matching — no LLM anywhere in this drill. It
also runs live on the learner's own deck nouns (``VocabCard.article`` is the
truth), which is what lets half a round be personal with zero enrichment.
"""

from functools import lru_cache
from pathlib import Path

import yaml

_RULES_PATH = Path(__file__).parent / "rules.yaml"
_POOLS_DIR = Path(__file__).parent / "pools"
_EXCEPTIONS_PATH = Path(__file__).parent / "exceptions.yaml"

ARTICLES = ("der", "die", "das")

# Concat-safe adjectives (stem+ending only, no elision like teuer→teure) —
# same philosophy and word list as ``drills/forge.py::SAFE_ADJECTIVES``; kept
# local because drill modules stay self-contained (and importing the forge
# would drag the LLM stack into content validation).
SAFE_ADJECTIVES = {
    "neu", "alt", "klein", "groß", "gut", "schön", "warm", "kalt", "frisch",
    "lang", "kurz", "schnell", "langsam", "billig", "ruhig", "laut", "leicht",
    "schwer", "voll", "leer", "sauber", "modern", "bequem", "wichtig",
    "lecker", "gesund", "stark", "nett", "freundlich", "bunt",
}

# The production beat accepts the phrase in definite OR indefinite form, in
# nominative OR accusative (the two cases the whitelisted carrier-sentence
# openers in routes.py can force deterministically). Table-built like
# drills/forge.py, so the gold forms can never be wrong: weak adjective
# endings after der-words, mixed after ein-words.
_DEF_ART = {
    "nominative": {"der": "der", "die": "die", "das": "das"},
    "accusative": {"der": "den", "die": "die", "das": "das"},
}
_WEAK_END = {
    "nominative": {"der": "e", "die": "e", "das": "e"},
    "accusative": {"der": "en", "die": "e", "das": "e"},
}
_EIN_ART = {
    "nominative": {"der": "ein", "die": "eine", "das": "ein"},
    "accusative": {"der": "einen", "die": "eine", "das": "ein"},
}
_MIXED_END = {
    "nominative": {"der": "er", "die": "e", "das": "es"},
    "accusative": {"der": "en", "die": "e", "das": "es"},
}


def phrase_forms(
    article: str, adjective: str, noun: str, case: str
) -> dict[str, tuple[str, str, str]]:
    """The accepted gold triples for one case, lowercased for comparison:
    ``{"definite": ("der", "bequeme", "stuhl"), "indefinite": ("ein",
    "bequemer", "stuhl")}``."""
    n = noun.lower()
    return {
        "definite": (
            _DEF_ART[case][article],
            adjective + _WEAK_END[case][article],
            n,
        ),
        "indefinite": (
            _EIN_ART[case][article],
            adjective + _MIXED_END[case][article],
            n,
        ),
    }


_RULE_REQUIRED = ("id", "kind", "match", "article", "reliability", "anchor", "examples")


@lru_cache(maxsize=1)
def load_rules() -> dict[str, dict]:
    """Parse and validate the ending-rule catalog once; return ``{id: rule}``."""
    with open(_RULES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = (data or {}).get("rules")
    if not rules:
        raise ValueError(f"{_RULES_PATH}: no 'rules' list")

    catalog: dict[str, dict] = {}
    for i, rule in enumerate(rules):
        where = f"{_RULES_PATH}: rules[{i}] ({rule.get('id', '?')})"
        for field in _RULE_REQUIRED:
            if not rule.get(field):
                raise ValueError(f"{where}: missing '{field}'")
        if rule["id"] in catalog:
            raise ValueError(f"{where}: duplicate rule id")
        if rule["kind"] not in ("suffix", "prefix"):
            raise ValueError(f"{where}: kind must be suffix|prefix")
        if rule["article"] not in ARTICLES:
            raise ValueError(f"{where}: article must be one of {ARTICLES}")
        for key in ("match", "examples"):
            if not isinstance(rule[key], list) or not all(
                isinstance(m, str) and m for m in rule[key]
            ):
                raise ValueError(f"{where}: '{key}' must be a list of strings")
        if any(m != m.lower() for m in rule["match"]):
            raise ValueError(f"{where}: 'match' strings must be lowercase")
        unless = rule.get("unless", [])
        if not isinstance(unless, list) or not all(isinstance(u, str) for u in unless):
            raise ValueError(f"{where}: 'unless' must be a list of strings")
        catalog[rule["id"]] = rule
    return catalog


def _match_surface(noun: str, rule: dict, *, require_stem: bool) -> str | None:
    """The cased substring of ``noun`` this rule fires on, or ``None``.

    ``require_stem=True`` (the auto-classifier) additionally demands at least
    one stem character outside the match — "Ei" is not an -ei word to a
    classifier, but a curated item may still hand-tag it as the trap it is.
    """
    lower = noun.lower()
    for m in rule["match"]:
        if require_stem and len(lower) <= len(m):
            continue
        if rule["kind"] == "suffix":
            if not lower.endswith(m):
                continue
            if any(lower.endswith(u) for u in rule.get("unless", ())):
                continue
            return noun[len(noun) - len(m):]
        else:  # prefix
            if not lower.startswith(m):
                continue
            return noun[: len(m)]
    return None


@lru_cache(maxsize=1)
def _classifier_rules() -> tuple[dict, ...]:
    """Classifiable rules, longest match first — so -ment beats -ent and -ie
    beats -e, whichever way the article comparison then falls."""
    rules = [r for r in load_rules().values() if r.get("classify", True)]
    return tuple(sorted(rules, key=lambda r: -max(len(m) for m in r["match"])))


def classify_noun(noun: str, article: str) -> tuple[str | None, str | None, bool]:
    """Classify one noun against the ending rules: ``(rule_id, surface, trap)``.

    A rule whose article AGREES with the noun's real article always wins
    (Gebäude: Ge- confirms das, the -e that suggests die loses). If only
    disagreeing rules match, the longest one names the deception and the noun
    is a trap (der Name "looks like" -e). No match at all → ``(None, None,
    False)`` — Track B, pure memory.
    """
    confirm: tuple[str, str] | None = None
    deceive: tuple[str, str] | None = None
    for rule in _classifier_rules():
        surface = _match_surface(noun, rule, require_stem=True)
        if surface is None:
            continue
        if rule["article"] == article:
            if confirm is None:
                confirm = (rule["id"], surface)
        elif deceive is None:
            deceive = (rule["id"], surface)
    if confirm is not None:
        return confirm[0], confirm[1], False
    if deceive is not None:
        return deceive[0], deceive[1], True
    return None, None, False


_ITEM_REQUIRED = ("id", "noun", "article", "gloss", "adjective")
_POOL_REQUIRED = ("id", "title", "position")


def _validate_item(item: dict, where: str, rules: dict[str, dict]) -> None:
    """Field + classifier-cross-check validation for one curated item —
    raises with a located message on the first problem."""
    for field in _ITEM_REQUIRED:
        if not item.get(field):
            raise ValueError(f"{where}: missing '{field}'")
    if item["article"] not in ARTICLES:
        raise ValueError(f"{where}: article must be one of {ARTICLES}")
    if item["adjective"] not in SAFE_ADJECTIVES:
        raise ValueError(
            f"{where}: adjective {item['adjective']!r} is not concat-safe"
        )

    rule_id = item.get("rule")
    trap = bool(item.get("trap"))
    why = item.get("why")
    if trap and not why:
        raise ValueError(f"{where}: traps need a 'why' teaching line")
    if not trap and why:
        raise ValueError(f"{where}: 'why' is traps-only")

    if rule_id is not None:
        rule = rules.get(rule_id)
        if rule is None:
            raise ValueError(f"{where}: unknown rule {rule_id!r}")
        if _match_surface(item["noun"], rule, require_stem=False) is None:
            raise ValueError(
                f"{where}: rule {rule_id!r} never matches {item['noun']!r}"
            )
        mismatch = rule["article"] != item["article"]
        if trap != mismatch:
            raise ValueError(
                f"{where}: trap={trap} but rule article "
                f"{rule['article']!r} vs item article {item['article']!r}"
            )
        # Keep the YAML honest: the tag must agree with what the live
        # classifier would say about a deck noun with the same shape.
        # Two legitimate exceptions: auto None (stem-guard words like Ei),
        # and a curated classify:false rule — verbnomen exists BECAUSE
        # string shape can't see it (das Kochen literally ends in -chen);
        # there the author's tag overrides the classifier on purpose.
        auto_rule, _, auto_trap = classify_noun(item["noun"], item["article"])
        if (
            auto_rule is not None
            and rule.get("classify", True)
            and (auto_rule, auto_trap) != (rule_id, trap)
        ):
            raise ValueError(
                f"{where}: auto-classifier disagrees — it says "
                f"({auto_rule!r}, trap={auto_trap})"
            )
    else:
        if trap:
            raise ValueError(f"{where}: a trap needs the rule it fakes")
        auto_rule, _, _ = classify_noun(item["noun"], item["article"])
        if auto_rule is not None:
            raise ValueError(
                f"{where}: tagged pattern-free but the classifier finds "
                f"{auto_rule!r} — tag it"
            )


@lru_cache(maxsize=1)
def load_pools() -> dict[str, dict]:
    """Parse and validate every pool under ``genus/pools/*.yaml`` once;
    return ``{pool_id: {"id", "title", "position", "items": {item_id:
    item}}}``. Item ids AND nouns are unique across all pools — one word,
    one home — which also keeps the exceptions-overlap check unambiguous."""
    rules = load_rules()
    files = sorted(_POOLS_DIR.glob("*.yaml"))
    if not files:
        raise ValueError(f"{_POOLS_DIR}: no pool files")

    pools: dict[str, dict] = {}
    seen_item_ids: set[str] = set()
    seen_nouns: dict[str, str] = {}  # noun.lower() -> pool id
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        meta = (data or {}).get("pool")
        items = (data or {}).get("items")
        if not isinstance(meta, dict):
            raise ValueError(f"{path}: missing 'pool' metadata block")
        for field in _POOL_REQUIRED:
            if meta.get(field) is None:
                raise ValueError(f"{path}: pool metadata missing '{field}'")
        if meta["id"] != path.stem:
            raise ValueError(
                f"{path}: pool id {meta['id']!r} must match the filename"
            )
        if meta["id"] in pools:
            raise ValueError(f"{path}: duplicate pool id")
        if not items:
            raise ValueError(f"{path}: no 'items' list")

        catalog: dict[str, dict] = {}
        for i, item in enumerate(items):
            where = f"{path}: items[{i}] ({item.get('id', '?')})"
            _validate_item(item, where, rules)
            if item["id"] in seen_item_ids:
                raise ValueError(f"{where}: duplicate item id across pools")
            noun_key = item["noun"].lower()
            if noun_key in seen_nouns:
                raise ValueError(
                    f"{where}: noun already in pool {seen_nouns[noun_key]!r} "
                    f"— one word, one home"
                )
            seen_item_ids.add(item["id"])
            seen_nouns[noun_key] = meta["id"]
            catalog[item["id"]] = item

        pools[meta["id"]] = {
            "id": meta["id"],
            "title": meta["title"],
            "position": int(meta["position"]),
            "items": catalog,
        }

    if "basis" not in pools:
        raise ValueError(f"{_POOLS_DIR}: the default 'basis' pool is required")
    return pools


@lru_cache(maxsize=1)
def load_items() -> dict[str, dict]:
    """All curated items across every pool, flat ``{id: item}`` — attempt
    resolution and the exceptions-overlap check are pool-agnostic."""
    flat: dict[str, dict] = {}
    for pool in load_pools().values():
        flat.update(pool["items"])
    return flat


@lru_cache(maxsize=1)
def load_exceptions() -> dict[str, dict]:
    """Parse and validate the exception lexicon once; return ``{noun.lower():
    entry}``. Every entry must actually be an ending-trap per the classifier
    (a wrong-article word no rule matches doesn't belong here — it would
    never be consulted), and must not duplicate a curated items.yaml noun
    (one source of truth per word: the curated item's own ``why`` wins)."""
    with open(_EXCEPTIONS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entries = (data or {}).get("exceptions")
    if not entries:
        raise ValueError(f"{_EXCEPTIONS_PATH}: no 'exceptions' list")

    curated_nouns = {i["noun"].lower() for i in load_items().values()}
    catalog: dict[str, dict] = {}
    for i, entry in enumerate(entries):
        where = f"{_EXCEPTIONS_PATH}: exceptions[{i}] ({entry.get('noun', '?')})"
        for field in ("noun", "article", "why"):
            if not entry.get(field):
                raise ValueError(f"{where}: missing '{field}'")
        key = entry["noun"].lower()
        if key in catalog:
            raise ValueError(f"{where}: duplicate noun")
        if key in curated_nouns:
            raise ValueError(
                f"{where}: already curated in items.yaml — keep one why per word"
            )
        if entry["article"] not in ARTICLES:
            raise ValueError(f"{where}: article must be one of {ARTICLES}")
        _, _, trap = classify_noun(entry["noun"], entry["article"])
        if not trap:
            raise ValueError(
                f"{where}: not an ending-trap per the classifier — "
                f"this entry would never be consulted"
            )
        catalog[key] = entry
    return catalog


@lru_cache(maxsize=1)
def _curated_trap_whys() -> dict[str, dict]:
    """Curated trap teaching lines indexed by noun — so a learner who decks
    a noun we also curate (die Mutter, der Kuchen) still gets the hand-
    written line: the round dedupes in the deck copy's favor, which would
    otherwise drop the curated ``why`` on the floor."""
    return {
        i["noun"].lower(): i
        for i in load_items().values()
        if i.get("trap")
    }


def trap_why(noun: str, article: str) -> str | None:
    """The hand-written teaching line for a trap noun, if we have one —
    exception lexicon first, curated items second. The article must agree
    (der See and die See are different words), else ``None``."""
    for source in (load_exceptions(), _curated_trap_whys()):
        entry = source.get(noun.lower())
        if entry is not None and entry["article"] == article:
            return entry["why"]
    return None
