// Thin authed client for the /szenario backend routes (szenario/routes.py) —
// same Bearer-replay + 401-signout + 422-reject contract as the other
// practice clients.
import { HTTP_BASE } from "@/lib/api";
import { UnauthorizedError, WordRejectedError } from "../satzschmiede/api";

// Re-exported so callers only need this module for the szenario flow.
export { UnauthorizedError };

export type Persona = {
  name: string;
  role: string;
  attitude: "friendly";
};

// One in-character question as the backend serves it. `zielVokabular` is
// part of the contract but not surfaced in this P1 thin slice — the learner
// answers cold, no vocab hint shown.
export type Scenario = {
  scenarioId: string;
  // VARY-001: index into the scenario's server-side `questions` list — the
  // client's half of a "scenarioId:questionIndex" seen-token. Absent only
  // against an old backend that predates variety tracking.
  questionIndex?: number;
  persona: Persona;
  kontext: string;
  question: string;
  zielVokabular: string[];
  // VARY-001: true when the server's seen-pool was exhausted and reset for
  // this pick — the client should clear its stored seen-token list.
  cycleReset?: boolean;
  // SZEN-007: the question tier actually served (server-derived from the
  // account level, `users.level`) — authoritative for which per-tier
  // seen-token bucket the served token gets written into. Absent only
  // against an old backend that predates account-level tiers.
  tier?: "a1" | "a2" | "b1" | "b2";
};

export type SentenceRead = {
  text: string;
  weight: "light" | "medium" | "heavy";
  simpler: string | null; // non-null: a lighter way to say an overloaded sentence
};

// The coach verdict for one spoken answer (POST /szenario/attempts) — this
// exercise coaches toward simple B1/B2 sentence architecture, never grammar.
export type StructureResult = {
  transcript: string;
  verdict: "clear" | "a_bit_heavy" | "overcomplicated";
  levelRead: string;
  coachMessage: string;
  sentences: SentenceRead[];
  skeleton: {
    kern: string;
    punkte: string[];
    absprung: string;
    vokabelAnker: string[];
  };
  // FLOW-006: present and true only on a client-synthesized "gave up"
  // result (see Flow.tsx's give-up handler) — the trainer uses it to show a
  // modest "gave up" state instead of the full verdict-card UI a real
  // attempt would render. There is no backend /szenario/give-up route (the
  // other Flow drills each have one) — this stays client-side for now.
  gaveUp?: boolean;
};

// FLOW-006: one item as GET /szenario/round serves it — same shape as
// `Scenario` minus `tier` (which moves up to the batch envelope below,
// shared by the whole draw).
export type SzenarioRoundItem = {
  scenarioId: string;
  questionIndex: number;
  persona: Persona;
  kontext: string;
  question: string;
  zielVokabular: string[];
  // VARY-001: true when this draw exhausted and reset the server's
  // seen-pool — the client should fold this into its stored seen-token list
  // by REPLACING it with just this item's own token rather than appending.
  cycleReset?: boolean;
};

// FLOW-006: GET /szenario/round's response — a small prefetch batch for the
// Flow's bag, `n` items drawn in one call instead of one at a time. `tier`
// is shared by the whole batch (every item is drawn from the same tier).
export type SzenarioRound = {
  tier: "a1" | "a2" | "b1" | "b2";
  items: SzenarioRoundItem[];
};

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
  if (res.status === 422) {
    // Learner-facing sentence from the backend ("We couldn't hear
    // anything…") — show it verbatim, same contract as Satzschmiede.
    const detail = (await res.json().catch(() => null))?.detail;
    throw new WordRejectedError(
      typeof detail === "string" ? detail : "We couldn't hear anything — try again."
    );
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `${path} failed (${res.status})`
    );
  }
  return res.json() as Promise<T>;
}

export async function fetchScenario(
  token: string,
  // VARY-001: "scenarioId:questionIndex" tokens already served this pool
  // cycle for the TIER the caller expects to be served (SZEN-007; see
  // Szenario.tsx for the account-level -> tier guess and per-tier
  // localStorage keys) — omit or pass empty for the old stateless draw.
  seen?: string[]
): Promise<Scenario> {
  const params: string[] = [];
  if (seen && seen.length > 0) {
    params.push(`seen=${encodeURIComponent(seen.join(","))}`);
  }
  const qs = params.length > 0 ? `?${params.join("&")}` : "";
  return request<Scenario>(`/szenario/scenario${qs}`, token);
}

// FLOW-006: the Flow's prefetch batch — `n` scenario/question draws in one
// call instead of the standalone page's one-at-a-time fetchScenario above.
// Same per-tier "scenarioId:questionIndex" seen-token contract (VARY-001,
// SZEN-007); see szenario/seen.ts for the shared storage helpers.
export async function fetchSzenarioRound(
  token: string,
  seen?: string[],
  n = 3
): Promise<SzenarioRound> {
  const params: string[] = [`n=${n}`];
  if (seen && seen.length > 0) {
    params.push(`seen=${encodeURIComponent(seen.join(","))}`);
  }
  return request<SzenarioRound>(`/szenario/round?${params.join("&")}`, token);
}

export async function submitAttempt(
  token: string,
  scenarioId: string,
  question: string,
  audio: Blob,
  // OBS-007: the practice-sitting id (minted by the Szenario shell on the
  // first attempt) — the backend files the attempt's Langfuse trace under it.
  sessionId: string
): Promise<StructureResult> {
  // No Content-Type header: the browser sets the multipart boundary itself.
  const form = new FormData();
  form.append("audio", audio, "attempt");
  form.append("scenarioId", scenarioId);
  form.append("question", question);
  form.append("sessionId", sessionId);
  return request("/szenario/attempts", token, { method: "POST", body: form });
}
