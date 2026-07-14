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
  persona: Persona;
  kontext: string;
  question: string;
  zielVokabular: string[];
};

export type SentenceRead = {
  text: string;
  color: "green" | "yellow" | "red";
  cut: string | null; // non-null: where an overloaded sentence should split
};

// The structure verdict for one spoken answer (POST /szenario/attempts) —
// this exercise judges shape (anchor / one-idea-per-sentence / clean close),
// never grammar.
export type StructureResult = {
  transcript: string;
  anchor: { present: boolean; note: string };
  sentences: SentenceRead[];
  close: { clean: boolean; note: string };
  skeleton: {
    kern: string;
    punkte: string[];
    absprung: string;
    vokabelAnker: string[];
  };
  takeaway: string;
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

export async function fetchScenario(token: string): Promise<Scenario> {
  return request<Scenario>("/szenario/scenario", token);
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
