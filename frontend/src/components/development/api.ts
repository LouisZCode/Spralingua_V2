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

export type DevelopmentStats = {
  week: PeriodStats;
  prevWeek: PeriodStats;
  today: TodayStats;
  focus: FocusPattern[];
  retired: RetiredPattern[];
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
