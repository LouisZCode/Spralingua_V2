// Thin authed client for the /zeitfaerbung backend routes
// (zeitfaerbung/routes.py) — same contract as the other practice clients.
import { HTTP_BASE } from "@/lib/api";
import { InsufficientCoinsError } from "@/lib/coins";
import { UnauthorizedError } from "../satzschmiede/api";

// One item as the round serves it. The answer stays server-side until the
// verdict. `hint` is an English rendering of the sentence — deliberately
// ABSENT on ambiguous items (both war and wurde would be correct, so an
// English gloss would give away which reading the item wants).
export type ZeitItem = {
  id: string;
  frame: string; // the sentence with one ___ gap over the verb
  hint?: string;
};

export type ZeitVerdict = {
  correct: boolean;
  // "match" = right verb, right form; "form" = right verb, wrong conjugation;
  // "verb" = wrong verb entirely; "unrecognized" = the answer couldn't be
  // parsed as an attempt at all (typo/gibberish) — no scoring on that kind.
  kind: "match" | "form" | "verb" | "unrecognized";
  expected: string; // the (a) correct form
  accepted: string[]; // all accepted forms (1 or 2)
  note: string | null; // explanation / guidance line
  // Only set on correct ambiguous items: meaning of the form the learner chose.
  reading: string | null;
  // Only set on correct ambiguous items: the OTHER valid form + its meaning.
  alt: { form: string; reading: string } | null;
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

export async function fetchRound(token: string): Promise<ZeitItem[]> {
  const data = await request<{ items: ZeitItem[] }>(
    "/zeitfaerbung/round",
    token
  );
  return data.items;
}

export async function submitAttempt(
  token: string,
  itemId: string,
  answer: string,
  // OBS-007: the practice-sitting id (minted by the Zeitfaerbung shell).
  sessionId: string
): Promise<ZeitVerdict> {
  return request("/zeitfaerbung/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId, answer, session_id: sessionId }),
  });
}

// FLOW-002: the deliberate "give up" escape (Flow mode only) — same endpoint,
// `give_up: true` skips recognition and grades a real, distinguishable miss.
export async function giveUp(
  token: string,
  itemId: string,
  sessionId: string
): Promise<ZeitVerdict> {
  return request("/zeitfaerbung/attempts", token, {
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
