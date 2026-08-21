// VARY-001/SZEN-007: per-tier "scenarioId:questionIndex" seen-token storage,
// shared by the standalone Szenario page and the Flow (FLOW-006) — extracted
// from Szenario.tsx so both callers read/write the exact same localStorage
// contract instead of drifting into two copies.

// SZEN-007: the question tier is read server-side from the learner's account
// level (`users.level`, LEVEL-001) and echoed back as `tier` on both
// GET /szenario/scenario and GET /szenario/round. The seen-token storage
// stays per-tier (a token's question index only makes sense against the
// list it was drawn from), keyed by that tier. The b1/b2 keys are the same
// ones SZEN-005 already used, kept for continuity with existing
// localStorage; a1/a2 are new.
export type Tier = "a1" | "a2" | "b1" | "b2";

// VARY-001: "scenarioId:questionIndex" tokens already served this pool
// cycle, kept in localStorage so variety persists across page visits.
// Guarded the same way as the rest of the codebase (AuthContext.tsx,
// SetupView.tsx): try/catch, only ever touched outside the render path.
const SEEN_STORAGE_KEY = "szenario-seen-v1";

// LEVEL-001/LEVEL-002 bucket -> the tier this trainer requests before the
// server confirms — same mapping szenario/routes.py applies for real. B2+
// maps to "b2" (not a "b1"-shaped fallback); no level set (or AuthContext
// hasn't hydrated yet) guesses the base "b1" tier, same as the backend's own
// no-level fallback.
const BUCKET_TO_TIER: Record<string, Tier> = {
  A1: "a1",
  A2: "a2",
  B1: "b1",
  "B2+": "b2",
};

export function expectedTier(level: string | null | undefined): Tier {
  return (level && BUCKET_TO_TIER[level]) || "b1";
}

export function seenKey(tier: Tier): string {
  switch (tier) {
    case "a1":
      return "szenario-seen-a1-v1";
    case "a2":
      return "szenario-seen-a2-v1";
    case "b2":
      return "szenario-seen-b2-v1";
    default:
      return SEEN_STORAGE_KEY; // "b1" — the original, pre-tier key.
  }
}

export function readSeen(tier: Tier): string[] {
  try {
    const raw = localStorage.getItem(seenKey(tier));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((x): x is string => typeof x === "string")
      : [];
  } catch {
    return [];
  }
}

export function writeSeen(tier: Tier, list: string[]): void {
  try {
    localStorage.setItem(seenKey(tier), JSON.stringify(list));
  } catch {
    // Storage blocked/unavailable — variety just resets next visit.
  }
}
