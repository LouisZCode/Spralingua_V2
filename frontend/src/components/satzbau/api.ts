// Thin authed client for the /satzbau backend routes (satzbau/routes.py) —
// same contract as the other practice clients (see faelle/api.ts).
import { HTTP_BASE } from "@/lib/api";
import { InsufficientCoinsError } from "@/lib/coins";
import { UnauthorizedError } from "../satzschmiede/api";

// One clause-building item as the round serves it. `answer`/`accepts`/
// `rule` stay server-side until the verdict — the rule line would answer
// the item. `chips` arrives pre-shuffled per serve.
export type ClauseItem = {
  id: string;
  given: string; // the fixed, unmovable lead-in shown above the chips ("" when the whole sentence is chips)
  task: string; // one English line naming the meaning to build
  chips: string[]; // the word tiles, shuffled
  // One English line pointing at the decision (never the answer) — primes
  // L1 interference, so the trainer hides it until asked and always
  // reveals it at grading (same "Show English" contract as Fälle's hint).
  hint: string;
};

export type ClauseVerdict = {
  correct: boolean;
  expected: string[]; // the canonical correct order
  rule: string; // the structure rule to learn, shown with the verdict either way
  // One ≤14-word English line naming exactly where the structure broke;
  // null when correct.
  note: string | null;
  // Set ONLY when correct AND the built order differs from `expected` — one
  // short English line on how the two read differently / which a German
  // would more likely say. Null otherwise.
  variant: string | null;
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

export async function fetchRound(token: string): Promise<ClauseItem[]> {
  const data = await request<{ items: ClauseItem[] }>("/satzbau/round", token);
  return data.items;
}

export async function submitAttempt(
  token: string,
  itemId: string,
  order: string[],
  // OBS-007: the practice-sitting id (minted by the Satzbau shell).
  sessionId: string
): Promise<ClauseVerdict> {
  return request("/satzbau/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId, order, session_id: sessionId }),
  });
}

// FLOW-002: the deliberate "give up" escape (Flow mode only) — same
// endpoint, `give_up: true` skips judging and grades a real, distinguishable
// miss.
export async function giveUp(
  token: string,
  itemId: string,
  sessionId: string
): Promise<ClauseVerdict> {
  return request("/satzbau/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: itemId,
      order: [],
      give_up: true,
      session_id: sessionId,
    }),
  });
}
