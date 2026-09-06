// Thin authed client for the /me/stats backend route — the learner's
// Development page. Same Bearer-replay + 401-signout contract as the other
// practice clients (mirrors szenario/api.ts).
import { HTTP_BASE } from "@/lib/api";
import { UnauthorizedError } from "../satzschmiede/api";

// Re-exported so callers only need this module for the Development flow.
export { UnauthorizedError };

// The ten practice modes that report into /me/stats.
export type ExerciseKey =
  | "satz"
  | "verbformen"
  | "sprechen"
  | "bauteil"
  | "verbindungen"
  | "szenario"
  | "genus"
  | "zeitfaerbung"
  | "faelle"
  | "satzbau";

export type ExerciseStat = {
  exercise: ExerciseKey;
  attempts: number;
  correct: number;
  // null for exercises with no right/wrong notion (e.g. szenario).
  accuracy: number | null;
};

export type ErrorExample = {
  sentence: string;
  // The ledger ring buffer can hold a slip whose correction was never
  // stored — render the sentence plain in that case (a null here crashed
  // the whole page pre-2026-07-31).
  corrected: string | null;
} | null;

export type TopError = {
  patternId: string;
  label: string;
  count: number;
  example: ErrorExample;
};

export type PeriodStats = {
  attemptsTotal: number;
  exercises: ExerciseStat[];
  topErrors: TopError[];
};

export type TodayStats = {
  attemptsTotal: number;
  topErrors: TopError[];
};

export type FocusPattern = {
  patternId: string;
  label: string;
  description: string;
  wrong?: string; // CLARA-19: taxonomy example pair, optional (stale-API fallback)
  right?: string;
  count7d: number;
  lifetime: number;
};

// DEV-002: retired patterns now carry their own story — when the pattern
// first/last surfaced and the learner's own sentences from the ledger's slip
// ring buffer (first = oldest slip we still hold, last = newest). Both
// example fields are null when the ledger never stored a full
// sentence+correction pair for the pattern.
export type RetiredSlipExample = {
  sentence: string;
  corrected: string;
};

export type RetiredPattern = {
  patternId: string;
  label: string;
  firstSeen: string | null; // ISO (ledger TIMESTAMP, no UTC offset)
  lastSeen: string | null;
  firstExample: RetiredSlipExample | null;
  lastExample: RetiredSlipExample | null;
};

// DATA-005: one daily bucket of the attempts chart. Weeks are derived
// client-side from the same series.
export type SeriesPoint = {
  date: string; // "2026-07-24" (UTC day)
  attempts: number;
  mistakes: number;
  firstTryCorrect: number;
};

// GAME-001: forgiving daily streak, one free weekly grace day, longest is a
// permanent PR that never resets. A day is earned by completing 3 of the 4
// learner-facing practice modes on /practice (satz, flow, tandem,
// briefkasten) — a single graded attempt no longer credits the day on its
// own. `practicedToday` now means the day is earned (modesRequired reached);
// `modesToday` is which of the four already count, server-ordered.
export type Streak = {
  current: number;
  longest: number;
  practicedToday: boolean;
  modesToday: PracticeMode[];
  modesRequired: number;
};

// GAME-001: the four practice modes that count toward the daily streak.
// Clara (the teacher) is deliberately not one of these — her card is exempt
// from the streak entirely.
export type PracticeMode = "satz" | "flow" | "tandem" | "briefkasten";

export type DevelopmentStats = {
  week: PeriodStats;
  prevWeek: PeriodStats;
  today: TodayStats;
  focus: FocusPattern[];
  retired: RetiredPattern[];
  series: SeriesPoint[];
  streak: Streak;
};

export async function fetchStats(token: string): Promise<DevelopmentStats> {
  const res = await fetch(`${HTTP_BASE}/me/stats`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    throw new UnauthorizedError("/me/stats");
  }
  if (!res.ok) {
    throw new Error(`/me/stats failed (${res.status})`);
  }
  return res.json() as Promise<DevelopmentStats>;
}

// GAME-001: fire-and-forget progress ping for the two modes that can't be
// credited server-side from a single backend call (satz/flow — tandem and
// briefkasten are credited server-side on their own). Posting a mode with no
// matching attempt logged today is a silent no-op on the backend (200), and
// this client is just as silent on failure: a learner who just finished a
// round must never see or feel a broken streak ping. Swallow everything —
// network errors, non-OK statuses, all of it — nothing here is worth
// surfacing.
export async function postModeComplete(
  token: string,
  mode: "satz" | "flow"
): Promise<void> {
  try {
    await fetch(`${HTTP_BASE}/streak/mode`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ mode }),
    });
  } catch {
    // Non-fatal by design — see comment above.
  }
}

// ── REC-001: today's recommended pillar for the practice menu ─────────────
// null = no clear signal (or not enough active days this week) → no banner.

export type Recommendation = {
  pillar: "satz" | "flow" | "tandem";
  reason: string;
  patternLabel?: string;
};

export async function fetchRecommendation(
  token: string,
): Promise<Recommendation | null> {
  const res = await fetch(`${HTTP_BASE}/me/recommendation`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    throw new UnauthorizedError("/me/recommendation");
  }
  if (!res.ok) {
    throw new Error(`/me/recommendation failed (${res.status})`);
  }
  const body = (await res.json()) as { recommendation: Recommendation | null };
  return body.recommendation;
}
