// Thin authed client for the /me/stats backend route — the learner's
// Development page. Same Bearer-replay + 401-signout contract as the other
// practice clients (mirrors szenario/api.ts).
import { HTTP_BASE } from "@/lib/api";
import { UnauthorizedError } from "../satzschmiede/api";

// Re-exported so callers only need this module for the Development flow.
export { UnauthorizedError };

// The six practice modes that report into /me/stats.
export type ExerciseKey =
  | "satz"
  | "verbformen"
  | "sprechen"
  | "bauteil"
  | "verbindungen"
  | "szenario";

export type ExerciseStat = {
  exercise: ExerciseKey;
  attempts: number;
  correct: number;
  // null for exercises with no right/wrong notion (e.g. szenario).
  accuracy: number | null;
};

export type ErrorExample = {
  sentence: string;
  corrected: string;
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
  count7d: number;
  lifetime: number;
};

export type RetiredPattern = {
  patternId: string;
  label: string;
};

// DATA-005: one daily bucket of the attempts chart. Weeks are derived
// client-side from the same series.
export type SeriesPoint = {
  date: string; // "2026-07-24" (UTC day)
  attempts: number;
  mistakes: number;
  firstTryCorrect: number;
};

export type DevelopmentStats = {
  week: PeriodStats;
  prevWeek: PeriodStats;
  today: TodayStats;
  focus: FocusPattern[];
  retired: RetiredPattern[];
  series: SeriesPoint[];
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

// ── BUG-010 / UI-005 minimal: recent conversation sessions ────────────────
// Same wire shapes the tandem debrief stores in activity_session.error_eval
// (snake_case — stored verbatim, mirrors TandemDebriefModal's interfaces).

export type DebriefPattern = {
  pattern_id: string;
  label: string;
  elicited: boolean;
  produced_correctly: boolean;
  evidence: string;
  corrected: string;
  note: string;
  retired: boolean;
};

export type DebriefNewError = {
  pattern_id: string;
  label: string;
  sentence: string;
  corrected: string;
  note: string;
};

export type SessionDebrief = {
  // session_note exists on the wire but is Lena's private memory — never
  // rendered (same rule as TandemDebriefModal).
  patterns?: DebriefPattern[];
  new_errors?: DebriefNewError[];
};

export type RecentSession = {
  id: string;
  lessonId: string;
  startedAt: string; // ISO, UTC offset included
  endedAt: string;
  endedBy: "user" | "agent" | "crash" | null;
  debrief: SessionDebrief | null; // tandem only
};

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

export async function fetchSessions(token: string): Promise<RecentSession[]> {
  const res = await fetch(`${HTTP_BASE}/me/sessions`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    throw new UnauthorizedError("/me/sessions");
  }
  if (!res.ok) {
    throw new Error(`/me/sessions failed (${res.status})`);
  }
  const body = (await res.json()) as { sessions: RecentSession[] };
  return body.sessions;
}
