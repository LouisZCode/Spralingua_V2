// Thin authed client for the /satz backend routes (satz/routes.py). Every
// call replays the session JWT as a Bearer header — same AUTH-001 token the
// WS handshake and /say already use.
import { HTTP_BASE } from "@/lib/api";
import type { Card } from "./deck";

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

// Thrown on 422: the backend rejected the input itself (not German, gibberish,
// a whole sentence). The detail is a learner-facing sentence written by the
// enricher — show it verbatim instead of a generic failure.
export class WordRejectedError extends Error {}

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

export async function fetchDeck(token: string): Promise<Card[]> {
  const data = await request<{ cards: Card[] }>("/satz/deck", token);
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

// The examiner's answer to one spoken attempt (POST /satz/attempts).
// `verdict` maps straight onto the trainer's card states; "revealed" stays a
// frontend-only state (the learner peeked instead of attempting).
export type AttemptResult = {
  transcript: string;
  verdict: "correct" | "close";
  feedback: string;
  corrected: string | null;
};

export async function submitAttempt(
  token: string,
  cardId: string,
  audio: Blob
): Promise<AttemptResult> {
  // No Content-Type header: the browser sets the multipart boundary itself.
  const form = new FormData();
  form.append("card_id", cardId);
  form.append("audio", audio, "attempt");
  return request("/satz/attempts", token, { method: "POST", body: form });
}

export async function removeCard(
  token: string,
  cardId: string
): Promise<{ removed: number; poolSize: number }> {
  return request(`/satz/deck/${encodeURIComponent(cardId)}`, token, {
    method: "DELETE",
  });
}
