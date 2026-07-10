// Thin authed client for the /satz backend routes (satz/routes.py). Every
// call replays the session JWT as a Bearer header — same AUTH-001 token the
// WS handshake and /say already use.
import { HTTP_BASE } from "@/lib/api";
import type { Card, DeckCard } from "./deck";

export type PackSummary = {
  id: string;
  title: string;
  description: string | null;
  kind: "level" | "situation";
  level: string | null;
  cardCount: number;
  ownedCount: number;
};

// Thrown on 401: the session JWT expired (no refresh in v1 — AUTH-001 says
// the frontend re-runs Google sign-in). Callers sign out instead of showing
// a generic error.
export class UnauthorizedError extends Error {
  constructor(path: string) {
    super(`${path} unauthorized — session expired`);
  }
}

// SATZ-005: when the rejected input is a real foreign word, the enricher names
// a German equivalent the learner can add with one tap instead of retyping.
export type WordSuggestion = {
  word: string;
  gloss: string | null;
  sourceLanguage: string | null;
};

// Thrown on 422: the backend rejected the input itself (not German, gibberish,
// a whole sentence). The message is a learner-facing sentence written by the
// enricher — show it verbatim. When `suggestion` is set, the input was a
// foreign word we can offer a German equivalent for (SATZ-005).
export class WordRejectedError extends Error {
  suggestion: WordSuggestion | null;
  constructor(message: string, suggestion: WordSuggestion | null = null) {
    super(message);
    this.suggestion = suggestion;
  }
}

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
    const detail = (await res.json().catch(() => null))?.detail;
    if (detail && typeof detail === "object") {
      // Structured reject (SATZ-005): a foreign word with a German equivalent.
      throw new WordRejectedError(
        typeof detail.message === "string"
          ? detail.message
          : "That input didn't work — try a single German word.",
        (detail.suggestion as WordSuggestion | undefined) ?? null
      );
    }
    throw new WordRejectedError(
      typeof detail === "string" ? detail : "That input didn't work — try a single German word."
    );
  }
  if (!res.ok) {
    throw new Error(`${path} failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function fetchPacks(token: string): Promise<PackSummary[]> {
  const data = await request<{ packs: PackSummary[] }>("/satz/packs", token);
  return data.packs;
}

export async function addPack(
  token: string,
  packId: string
): Promise<{ added: number; poolSize: number }> {
  return request(`/satz/packs/${packId}/add`, token, { method: "POST" });
}

export async function fetchDeck(token: string): Promise<DeckCard[]> {
  const data = await request<{ cards: DeckCard[] }>("/satz/deck", token);
  return data.cards;
}

export async function addWord(
  token: string,
  word: string
): Promise<{ card: Card; created: boolean; added: number; poolSize: number }> {
  return request("/satz/cards", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ word }),
  });
}

// The examiner's answer to one spoken attempt (POST /satz/attempts) — two
// separate judgements: the WORD is what the card tests (wordOk drives the
// green/red tint); the rest of the sentence's grammar is feedback only,
// rendered as a grammar note on a green card, never a fail. "revealed" stays
// a frontend-only state (the learner peeked instead of attempting).
export type AttemptResult = {
  transcript: string;
  wordOk: boolean;
  grammarOk: boolean;
  // One ≤10-word line naming the broken rule ("'weil' sends the verb to the
  // end") — always set on grammar errors, only-when-not-obvious on word errors.
  error: string | null;
  corrected: string | null;
  // When the card comes back: the interval the scheduler just wrote (0 =
  // still due today after a miss, 1 = tomorrow, then the expanding ladder).
  dueInDays: number;
};

export async function submitAttempt(
  token: string,
  cardId: string,
  audio: Blob,
  // OBS-007: the practice-sitting id (minted by VocabTrainer on the first
  // attempt) — the backend files the attempt's Langfuse trace under it.
  sessionId: string
): Promise<AttemptResult> {
  // No Content-Type header: the browser sets the multipart boundary itself.
  const form = new FormData();
  form.append("card_id", cardId);
  form.append("audio", audio, "attempt");
  form.append("session_id", sessionId);
  return request("/satz/attempts", token, { method: "POST", body: form });
}

// The learner peeked at the example instead of attempting — record the lapse
// so the reveal can't silently keep a long interval alive. The card drops to
// "due now"; reps stay untouched (a reveal isn't a graded attempt).
export async function revealCard(
  token: string,
  cardId: string
): Promise<{ dueInDays: number }> {
  return request(`/satz/deck/${encodeURIComponent(cardId)}/reveal`, token, {
    method: "POST",
  });
}

export async function removeCard(
  token: string,
  cardId: string
): Promise<{ removed: number; poolSize: number }> {
  return request(`/satz/deck/${encodeURIComponent(cardId)}`, token, {
    method: "DELETE",
  });
}
