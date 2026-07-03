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
