// Thin authed client for the /verbindungen backend routes
// (verbindungen/routes.py) — same contract as the other practice clients.
import { HTTP_BASE } from "@/lib/api";
import { InsufficientCoinsError } from "@/lib/coins";
import { UnauthorizedError } from "../satzschmiede/api";

// One chunk item as the round serves it. The answer AND the canonical chunk
// stay server-side until the verdict — the chunk line would answer the item.
export type ChunkItem = {
  id: string;
  frame: string; // the sentence with one ___ gap over the chunk region
  hint: string; // English rendering of the full sentence
};

export type ChunkVerdict = {
  correct: boolean;
  expected: string; // the words that fill the gap
  chunk: string; // the canonical chunk to memorize ("sich freuen auf + Akk")
  // One ≤14-word English line naming exactly which element broke (pronoun /
  // preposition / case / compound); null when correct.
  note: string | null;
  // GRAM-009: the taxonomy pattern this item drills — feeds FeedbackCard's
  // collapsed "Warum?" disclosure (GET /grammar/pattern/{id}). camelCase
  // like every other practice payload in this repo.
  patternId: string;
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

export async function fetchRound(token: string): Promise<ChunkItem[]> {
  const data = await request<{ items: ChunkItem[] }>("/verbindungen/round", token);
  return data.items;
}

export async function submitAttempt(
  token: string,
  itemId: string,
  answer: string,
  // OBS-007: the practice-sitting id (minted by the Verbindungen shell).
  sessionId: string
): Promise<ChunkVerdict> {
  return request("/verbindungen/attempts", token, {
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
): Promise<ChunkVerdict> {
  return request("/verbindungen/attempts", token, {
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
