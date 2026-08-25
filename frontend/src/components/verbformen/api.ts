// Thin authed client for the /verbformen backend routes (verbformen/routes.py)
// — same Bearer-replay + 401-signout contract as the satzschmiede client,
// whose UnauthorizedError / WordRejectedError and payload types are reused so
// VocabTrainer plugs in unchanged. Only the routes differ: the deck auto-feeds
// past-tense verb cards from the Satzschmiede pool, while attempts, removals
// and reveals write the drill-local user_verbformen overlay — never the
// shared Satzschmiede schedule.
import { HTTP_BASE } from "@/lib/api";
import { InsufficientCoinsError } from "@/lib/coins";
import {
  UnauthorizedError,
  WordRejectedError,
  type AttemptResult,
} from "../satzschmiede/api";
import type { DeckCard } from "../satzschmiede/deck";

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
    // Learner-facing backend sentence ("We couldn't hear anything…") —
    // VocabTrainer shows WordRejectedError messages verbatim.
    const detail = (await res.json().catch(() => null))?.detail;
    throw new WordRejectedError(
      typeof detail === "string" ? detail : "That attempt didn't work — try again."
    );
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
    throw new Error(`${path} failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function fetchDeck(token: string): Promise<DeckCard[]> {
  const data = await request<{ cards: DeckCard[] }>("/verbformen/deck", token);
  return data.cards;
}

export async function submitAttempt(
  token: string,
  cardId: string,
  audio: Blob,
  // OBS-007: the practice-sitting id ("vf-…", minted by VocabTrainer on the
  // first attempt) — the backend files the attempt's Langfuse trace under it.
  sessionId: string
): Promise<AttemptResult> {
  // No Content-Type header: the browser sets the multipart boundary itself.
  const form = new FormData();
  form.append("card_id", cardId);
  form.append("audio", audio, "attempt");
  form.append("session_id", sessionId);
  return request("/verbformen/attempts", token, { method: "POST", body: form });
}

// Drill-local lapse: the card drops to "due now" in Verbformen only — its
// Satzschmiede schedule keeps whatever interval it had.
export async function revealCard(
  token: string,
  cardId: string
): Promise<{ dueInDays: number }> {
  return request(`/verbformen/deck/${encodeURIComponent(cardId)}/reveal`, token, {
    method: "POST",
  });
}

// Drill-local removal: hides the verb from the Verbformen deck; the verb (and
// its schedule) stay in the Satzschmiede pool untouched.
export async function removeCard(
  token: string,
  cardId: string
): Promise<{ removed: number }> {
  return request(`/verbformen/deck/${encodeURIComponent(cardId)}`, token, {
    method: "DELETE",
  });
}

export { UnauthorizedError, type AttemptResult };
