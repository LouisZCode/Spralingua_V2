// Thin authed client for the /bauteil backend routes (bauteil/routes.py) —
// same Bearer-replay + 401-signout contract as the satzschmiede client, whose
// UnauthorizedError is reused so one catch handles every practice module.
import { HTTP_BASE } from "@/lib/api";
import { UnauthorizedError } from "../satzschmiede/api";

// One drill item as the round serves it. The answer never ships — checking
// happens server-side (POST /bauteil/attempts), so devtools can't leak it.
export type RoundItem = {
  id: string;
  parts: string[]; // raw uninflected parts: ["ein", "gut", "Job"]
  frame: string; // the sentence with one ___ gap — it decides the case
  hint: string; // English rendering of the full sentence
};

// The two-axis verdict (GRAM-002 design rule 6): the CASE the learner aimed
// at (the row) and the CARRIER/endings execution (the column) are judged
// separately — conflating them teaches nothing.
export type BauteilVerdict = {
  correct: boolean;
  expected: string;
  caseOk: boolean;
  carrierOk: boolean;
  // One ≤14-word English line naming which axis broke and why; null when
  // correct (or when the deterministic exact-match check green-lit it).
  note: string | null;
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
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `${path} failed (${res.status})`
    );
  }
  return res.json() as Promise<T>;
}

export async function fetchRound(token: string): Promise<RoundItem[]> {
  const data = await request<{ items: RoundItem[] }>("/bauteil/round", token);
  return data.items;
}

export async function submitAttempt(
  token: string,
  itemId: string,
  answer: string,
  // OBS-007: the practice-sitting id (minted by the Bauteil shell on the
  // first attempt) — the backend files the attempt's Langfuse trace under it.
  sessionId: string
): Promise<BauteilVerdict> {
  return request("/bauteil/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId, answer, session_id: sessionId }),
  });
}
