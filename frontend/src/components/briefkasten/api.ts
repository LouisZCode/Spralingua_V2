// Thin authed client for the /briefkasten backend routes
// (briefkasten/routes.py) — same Bearer-replay + 401-signout + detail-surface
// contract as the other practice clients (see verbindungen/api.ts).
import { HTTP_BASE } from "@/lib/api";
import { InsufficientCoinsError } from "@/lib/coins";
import { UnauthorizedError } from "../satzschmiede/api";

// Re-exported so callers only need this module for the briefkasten flow.
export { UnauthorizedError };

export type Register = "informal" | "formal";
export type Level = "a1" | "a2" | "b1" | "b2";

// One incoming letter, minted per GET /briefkasten/letter — stateless by
// design (see routes.py's module docstring): the client echoes `body` and
// `points` back verbatim on both attempts rather than the server holding a
// session row for a two-attempt exercise.
export type Letter = {
  seedId: string;
  register: Register;
  level: Level;
  sender: { name: string; relation: string };
  betreff: string;
  body: string;
  points: string[]; // exactly 4, English
  wordTarget: { min: number; max: number };
  // VARY-001: true when the server's seen-pool was exhausted and reset for
  // this pick — the client clears its stored seen-id list.
  cycleReset: boolean;
};

// Attempt 1's verdict: where to look, never the fix. `attempt` discriminates
// the union — use it, not field presence, to tell the two verdicts apart.
export type HintItem = {
  category: "grammatik" | "wortschatz" | "rechtschreibung" | "wortstellung";
  text: string; // the learner's own phrase, quoted verbatim
  hint: string; // one English line pointing at the rule — never the answer
};

export type HintResult = {
  attempt: 1;
  items: HintItem[];
  message: string;
  coveredPoints: boolean[]; // exactly 4, one per `points` entry
};

// Attempt 2's verdict: the full correction pass.
export type Explanation = { error: string; correction: string; why: string };

export type FeedbackResult = {
  attempt: 2;
  correctedText: string;
  explanations: Explanation[];
  focusPoints: string[];
  score: number; // 0-100, judged against the letter's level
  feedback: string;
  improvementsFromFirst: string;
  // IDIOM-002: how a German would actually have written this letter in this
  // register. null is a first-class, common outcome — render nothing at all
  // when null, never an empty card.
  naturalVersion: string | null;
};

export type AttemptResult = HintResult | FeedbackResult;

async function request<T>(
  path: string,
  token: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${HTTP_BASE}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, ...(init?.headers ?? {}) },
  });
  if (res.status === 401) {
    throw new UnauthorizedError(path);
  }
  if (res.status === 402) {
    const body = await res.clone().json().catch(() => null);
    const detail = (body as { detail?: unknown })?.detail;
    if (detail && typeof detail === "object") {
      const d = detail as { code?: unknown; needed?: unknown; available?: unknown };
      if (d.code === "insufficient_coins" && typeof d.needed === "number" && typeof d.available === "number") {
        
        throw new InsufficientCoinsError(d.needed, d.available);
      }
    }
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `${path} failed (${res.status})`
    );
  }
  return res.json() as Promise<T>;
}

export async function fetchLetter(
  token: string,
  // VARY-001: seed ids already served this pool cycle (localStorage-backed,
  // see Briefkasten.tsx) — omit or pass empty for a plain draw.
  seen?: string[],
  // Filters the seed pool; omit (or "any") for the whole pool.
  register?: Register,
  // OBS-007 practice-sitting id, owned by the Briefkasten shell — same
  // contract as `submitAttempt`'s `sessionId` below, but this is a GET so it
  // rides as a query param instead of a JSON body field.
  sessionId?: string
): Promise<Letter> {
  const params: string[] = [];
  if (seen && seen.length > 0) {
    params.push(`seen=${encodeURIComponent(seen.join(","))}`);
  }
  if (register) {
    params.push(`register=${register}`);
  }
  if (sessionId) {
    params.push(`session_id=${encodeURIComponent(sessionId)}`);
  }
  const qs = params.length > 0 ? `?${params.join("&")}` : "";
  return request<Letter>(`/briefkasten/letter${qs}`, token);
}

export async function submitAttempt(
  token: string,
  params: {
    seedId: string;
    letterBody: string; // echoed back exactly as served
    points: string[]; // echoed back exactly as served
    response: string; // this attempt's text
    // Required when attempt === 2 — the judge compares the two drafts.
    firstAttempt?: string;
    attempt: 1 | 2;
    // OBS-007 practice-sitting id, minted by the Briefkasten shell.
    sessionId?: string;
  }
): Promise<AttemptResult> {
  return request("/briefkasten/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      seed_id: params.seedId,
      letter_body: params.letterBody,
      points: params.points,
      response: params.response,
      first_attempt: params.firstAttempt ?? null,
      attempt: params.attempt,
      session_id: params.sessionId ?? null,
    }),
  });
}
