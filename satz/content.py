"""Curated pack loader + validator for Satzschmiede (SATZ-002).

Reads every ``satz/packs/*.yaml`` and enforces the card rules the trainer's
pedagogy depends on (see ``.claude/skills/satz-cards/SKILL.md``): nouns carry
their article + gender/plural note, verbs carry NO grammar note (the learner
recalls case/reflexivity unaided), phrases carry a register note.

Fail-loud by design: a malformed pack raises ``ValueError`` at import of the
content, which aborts startup — same philosophy as ``init_engine``. Broken
curated content should never boot.
"""

from pathlib import Path

import yaml

PACKS_DIR = Path(__file__).parent / "packs"

CARD_TYPES = {"noun", "verb", "phrase"}
PACK_KINDS = {"level", "situation"}


def _validate_card(card: dict, pack_id: str) -> None:
    cid = card.get("id", "<missing id>")
    where = f"card '{cid}' in pack '{pack_id}'"

    for field in ("id", "type", "target", "gloss"):
        if not card.get(field):
            raise ValueError(f"{where}: missing required field '{field}'")
    if card["type"] not in CARD_TYPES:
        raise ValueError(f"{where}: type must be one of {sorted(CARD_TYPES)}")

    if card["type"] == "noun":
        if card.get("article") not in {"der", "die", "das"}:
            raise ValueError(f"{where}: nouns need article der/die/das")
        if not card.get("note"):
            raise ValueError(f"{where}: nouns need a gender·plural note")
    else:
        if card.get("article"):
            raise ValueError(f"{where}: only nouns may carry an article")

    # The Verbs Rule: the clue is the bare infinitive and NOTHING else — no
    # case, no preposition, no note. Reflexivity lives in the boolean flag.
    if card["type"] == "verb" and card.get("note"):
        raise ValueError(f"{where}: verb cards must not carry a note (Verbs Rule)")
    if card["type"] != "verb" and card.get("reflexive"):
        raise ValueError(f"{where}: 'reflexive' applies to verbs only")


def load_packs() -> tuple[list[dict], dict[str, dict]]:
    """Load and validate all pack files.

    Returns ``(packs, cards_by_id)``: each pack dict carries ``card_ids`` in
    YAML order; ``cards_by_id`` holds every unique card definition. A card is
    defined in exactly one file and reused elsewhere via ``- ref: <id>`` —
    a second full definition of the same id is an error, so content can't
    silently fork.
    """
    packs: list[dict] = []
    cards_by_id: dict[str, dict] = {}
    refs: list[tuple[str, str]] = []  # (pack_id, card_id) resolved after all files

    for path in sorted(PACKS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        pack_id = raw.get("id")
        if not pack_id:
            raise ValueError(f"{path.name}: pack is missing 'id'")
        if not raw.get("title"):
            raise ValueError(f"pack '{pack_id}': missing 'title'")
        if raw.get("kind") not in PACK_KINDS:
            raise ValueError(f"pack '{pack_id}': kind must be one of {sorted(PACK_KINDS)}")
        entries = raw.get("cards") or []
        if not entries:
            raise ValueError(f"pack '{pack_id}': has no cards")

        card_ids: list[str] = []
        for entry in entries:
            if "ref" in entry:
                refs.append((pack_id, entry["ref"]))
                card_ids.append(entry["ref"])
                continue
            _validate_card(entry, pack_id)
            cid = entry["id"]
            if cid in cards_by_id:
                raise ValueError(
                    f"card '{cid}' defined twice (second time in pack '{pack_id}') — "
                    f"use '- ref: {cid}' to reuse a card across packs"
                )
            cards_by_id[cid] = entry
            card_ids.append(cid)

        packs.append(
            {
                "id": pack_id,
                "title": raw["title"],
                "description": raw.get("description"),
                "kind": raw["kind"],
                "level": raw.get("level"),
                "position": raw.get("position", 0),
                "card_ids": card_ids,
            }
        )

    for pack_id, cid in refs:
        if cid not in cards_by_id:
            raise ValueError(f"pack '{pack_id}': ref '{cid}' matches no defined card")

    return packs, cards_by_id
