// Thin authed client for the /faelle backend routes (faelle/routes.py) —
// same contract as the other practice clients.
import { HTTP_BASE } from "@/lib/api";
import { UnauthorizedError } from "../satzschmiede/api";

// One case-drill item as the round serves it. The answer AND the rule stay
// server-side until the verdict — the rule line would answer the item.
export type CaseItem = {
  id: string;
  frame: string; // the sentence with one ___ gap over the case-decision region
  hint: string; // one English line pointing at the decision, never the answer
};

export type CaseVerdict = {
  correct: boolean;
  expected: string; // the words that fill the gap
  rule: string; // the case rule to learn ("mit always takes Dativ. …")
  // One ≤14-word English line naming exactly which case decision broke;
  // null when correct.
  note: string | null;
  // Set ONLY when the typed answer is itself grammatical German that means
  // something else (most often the other case of a two-way preposition) —
  // the highest-value feedback in the exercise. Null otherwise.
  meansInstead: string | null;
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

export async function fetchRound(token: string): Promise<CaseItem[]> {
  const data = await request<{ items: CaseItem[] }>("/faelle/round", token);
  return data.items;
}

export async function submitAttempt(
  token: string,
  itemId: string,
  answer: string,
  // OBS-007: the practice-sitting id (minted by the Fälle shell).
  sessionId: string
): Promise<CaseVerdict> {
  return request("/faelle/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId, answer, session_id: sessionId }),
  });
}

// FLOW-002: the deliberate "give up" escape (Flow mode only) — same endpoint,
// `give_up: true` skips judging and grades a real, distinguishable miss.
export async function giveUp(
  token: string,
  itemId: string,
  sessionId: string
): Promise<CaseVerdict> {
  return request("/faelle/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: itemId,
      answer: "",
      give_up: true,
      session_id: sessionId,
    }),
  });
}
