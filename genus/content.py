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
_ITEMS_PATH = Path(__file__).parent / "items.yaml"

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

# The production beat is nominative + indefinite article on purpose: it's the
# one frame where all three genders produce DIFFERENT surface forms (ein
# neuer / eine neue / ein neues) — definite articles would collapse the
# adjective to -e across the board. Cases beyond nominative are a later knob.
_EIN_NOM = {"der": "ein", "die": "eine", "das": "ein"}
_ADJ_NOM_END = {"der": "er", "die": "e", "das": "es"}


def build_phrase(article: str, adjective: str, noun: str) -> str:
    """The deterministic gold phrase — ein/eine + inflected adjective + noun."""
    return f"{_EIN_NOM[article]} {adjective}{_ADJ_NOM_END[article]} {noun}"


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


@lru_cache(maxsize=1)
def load_items() -> dict[str, dict]:
    """Parse and validate the curated noun catalog once; return ``{id: item}``."""
    rules = load_rules()
    with open(_ITEMS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    items = (data or {}).get("items")
    if not items:
        raise ValueError(f"{_ITEMS_PATH}: no 'items' list")

    catalog: dict[str, dict] = {}
    for i, item in enumerate(items):
        where = f"{_ITEMS_PATH}: items[{i}] ({item.get('id', '?')})"
        for field in _ITEM_REQUIRED:
            if not item.get(field):
                raise ValueError(f"{where}: missing '{field}'")
        if item["id"] in catalog:
            raise ValueError(f"{where}: duplicate item id")
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
            # auto None is legitimate only for stem-guard words (Ei) and
            # classify:false rules (verbnomen) — the surface check above
            # already vouched for those.
            auto_rule, _, auto_trap = classify_noun(item["noun"], item["article"])
            if auto_rule is not None and (auto_rule, auto_trap) != (rule_id, trap):
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

        catalog[item["id"]] = item
    return catalog
