"""Shared Cerebras strict-json_schema self-check for interview/judges/
modules that define their own Pydantic schema (comprehension.py,
goal_coverage.py). Ported verbatim from
``interview_local/judges/_schema_lint.py`` (INTV-003 slice 2).

Same checks as ``build/briefs.py``'s own ``_lint_cerebras_schema`` (that
module predates this one and keeps its private copy) and
``speedtest/strict_lint.py`` (gitignored/local, out of this package's
scope): root object, additionalProperties:false at every object level, none
of the keywords Cerebras' 2026-07-21 strict-mode enforcement bans
(pattern/format/minItems/maxItems/minLength/maxLength/$anchor), serialized
schema under 5000 chars (Field descriptions count). Call at import time --
fails loudly (raises) rather than letting a future edit silently produce a
schema Cerebras rejects at call time.
"""
import json

_FORBIDDEN_SCHEMA_KEYS = {
    "pattern", "format", "minItems", "maxItems", "minLength", "maxLength", "$anchor",
}
_MAX_SCHEMA_CHARS = 5000


def _walk_schema_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _walk_schema_keys(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_schema_keys(item)


def lint_cerebras_schema(model_cls) -> None:
    """Raises RuntimeError if `model_cls` violates any Cerebras strict
    json_schema constraint. Every model passed here must set
    ``model_config = ConfigDict(extra="forbid")`` -- that's what makes
    pydantic itself emit ``additionalProperties: false``, which this
    function then verifies rather than assumes."""
    schema = model_cls.model_json_schema()
    serialized = json.dumps(schema)
    errors = []

    if schema.get("type") != "object":
        errors.append(f"root schema type is {schema.get('type')!r}, expected 'object'")
    if schema.get("additionalProperties") is not False:
        errors.append("root schema additionalProperties is not False")
    for name, defn in (schema.get("$defs") or {}).items():
        if defn.get("type") == "object" and defn.get("additionalProperties") is not False:
            errors.append(f"$defs.{name}.additionalProperties is not False")

    banned_present = set(_walk_schema_keys(schema)) & _FORBIDDEN_SCHEMA_KEYS
    if banned_present:
        errors.append(f"forbidden schema keywords present: {sorted(banned_present)}")

    if len(serialized) >= _MAX_SCHEMA_CHARS:
        errors.append(f"schema serialized length {len(serialized)} >= {_MAX_SCHEMA_CHARS} char budget")

    if errors:
        raise RuntimeError(
            f"{model_cls.__name__} violates Cerebras strict json_schema constraints: "
            + "; ".join(errors)
        )
