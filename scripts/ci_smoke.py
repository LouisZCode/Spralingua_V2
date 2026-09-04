"""CI-001 import-smoke: ``import main`` succeeds and every router is mounted.

Run from the repo root as ``uv run python scripts/ci_smoke.py`` (locally) or
via ``.github/workflows/ci.yml`` (CI). The dummy env this needs is set by the
workflow step's ``env:`` block, not by this script — see the comment there
for why each var is required.

What this does NOT do, on purpose: it never calls ``init_engine`` or awaits
``main.lifespan`` — those only run under a real ASGI startup event (uvicorn,
or FastAPI's TestClient/``asgi-lifespan``), and a plain ``import main`` never
triggers them. That's what makes this smoke safe to run with no Postgres and
no real provider keys: everything Postgres-shaped (``init_engine``, the
curated-content sync, every drill catalog loader) lives inside
``main.py::lifespan``, not at module scope. See CLAUDE.md's "Fail-loud
startup" section and ``database/connection.py::init_engine``'s docstring.

One real requirement DOES live at plain-import time, outside the lifespan:
``agents/conversation_agent.py`` builds its module-level ``ChatOpenAI`` client
(``_model = _build_model()``) as soon as the module loads, and the ``openai``
SDK raises immediately if ``api_key`` is empty — so ``OPENROUTER_API_KEY``
must be set to *some* non-empty string (never validated against a real
provider at import time, so a dummy value is fine). That requirement isn't
declared anywhere in ``config/settings.py`` itself (which never raises at
import for any missing var) — it only surfaces two import-hops downstream, in
a module this script never edits. Losing that undeclared requirement is
exactly the kind of thing this smoke exists to catch.
"""

import sys
from pathlib import Path

# Running this as a script file (rather than `python -c` or `-m`) puts the
# `scripts/` directory on sys.path[0], not the repo root — so a bare
# `import main` would miss the repo-root main.py. Insert the repo root
# explicitly so this works from any cwd, matching how the workflow invokes it
# (`uv run python scripts/ci_smoke.py` from the repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Hardcoded from main.py's 19 `app.include_router(...)` calls (2026-09-04) —
# a router silently dropped from main.py (or one whose APIRouter prefix
# changes) must fail this list, not slide by unnoticed. Keep in sync with
# main.py when a router is added, removed, or re-prefixed.
#
# `stats` is the one router mounted with no APIRouter(prefix=...) at all —
# its routes are literal paths like "/me/stats" — so it's checked by exact
# route path instead of by prefix.
EXPECTED_PREFIXES = [
    "/auth",
    "/satz",
    "/bauteil",
    "/sprechen",
    "/verbformen",
    "/verbindungen",
    "/zeitfaerbung",
    "/faelle",
    "/satzbau",
    "/genus",
    "/szenario",
    "/briefkasten",
    "/idiom",
    "/interview",
    "/teacher",
    "/payments",
    "/coins",
    "/grammar",
]
EXPECTED_UNPREFIXED_ROUTE = "/me/stats"  # stands in for the stats router


def main() -> None:
    print("ci_smoke: importing main …")
    try:
        import main as app_module
    except Exception:
        print("ci_smoke: FAILED — `import main` raised:", file=sys.stderr)
        raise
    print("ci_smoke: import main OK")

    paths = sorted({r.path for r in app_module.app.routes if hasattr(r, "path")})
    if not paths:
        print("ci_smoke: FAILED — app.routes is empty", file=sys.stderr)
        sys.exit(1)

    missing = [
        prefix
        for prefix in EXPECTED_PREFIXES
        if not any(p == prefix or p.startswith(prefix + "/") for p in paths)
    ]
    if EXPECTED_UNPREFIXED_ROUTE not in paths:
        missing.append(f"{EXPECTED_UNPREFIXED_ROUTE} (stats router)")

    if missing:
        print(
            "ci_smoke: FAILED — expected router mount(s) missing from app.routes:\n  "
            + "\n  ".join(missing),
            file=sys.stderr,
        )
        print(f"ci_smoke: app.routes had {len(paths)} path(s):", file=sys.stderr)
        for p in paths:
            print(f"  {p}", file=sys.stderr)
        sys.exit(1)

    print(
        f"ci_smoke: OK — {len(EXPECTED_PREFIXES) + 1}/19 expected router mounts "
        f"found ({len(paths)} total route paths)"
    )


if __name__ == "__main__":
    main()
