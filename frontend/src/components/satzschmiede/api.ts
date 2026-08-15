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

// GET /satz/deck's full response: the pool plus today's server-decided
// new-word drip. `newAllowance` is final — reviews (due + already cleared
// today) have already claimed their share of the daily target, so callers
// just use the number as-is (5..20, see satz/routes.py's SESSION_TARGET/
// REVIEW_SHARE).
export type DeckPayload = {
  cards: DeckCard[];
  newAllowance: number;
};

export async function fetchDeck(token: string): Promise<DeckPayload> {
  return request<DeckPayload>("/satz/deck", token);
}

export async function addWord(
  token: string,
  word: string,
  // Practice-sitting id (OBS-007) — set when add-word is called mid-session
  // (e.g. from a rejected-attempt suggestion) so the backend can file the
  // card creation under the same Langfuse session as the rest of the sitting.
  sessionId?: string,
  // SATZ-013: "gloss" when this add came from the hover/tap GLOSS popover's
  // one-tap add button — the only path subject to the daily gloss-add cap.
  // Omitted by the manual add-word form (AddWordForm.tsx), which stays
  // uncapped.
  source?: "gloss"
): Promise<{
  card: Card;
  created: boolean;
  added: number;
  poolSize: number;
  // Present only when `source: "gloss"` was sent — remaining gloss-path
  // adds allowed today (0..3), already accounting for this call.
  glossRemaining?: number;
}> {
  return request("/satz/cards", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      word,
      ...(sessionId ? { session_id: sessionId } : {}),
      ...(source ? { source } : {}),
    }),
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
  // SATZ-008: trace id of this judgement — lets the client flag it.
  traceId?: string;
};

export async function submitAttempt(
  token: string,
  cardId: string,
  audio: Blob,
  // OBS-007: the practice-sitting id (minted by VocabTrainer on the first
  // attempt) — the backend files the attempt's Langfuse trace under it.
  sessionId: string,
  // SATZ-015: an immediate re-attempt of a card that just passed green with
  // a grammar correction. Judged identically (same STT + examiner), but the
  // backend skips every schedule/ledger/attempt-log write for it — the
  // original graded pass already stands. Omitted (default false/absent) on
  // every ordinary attempt.
  rehearsal?: boolean
): Promise<AttemptResult> {
  // No Content-Type header: the browser sets the multipart boundary itself.
  const form = new FormData();
  form.append("card_id", cardId);
  form.append("audio", audio, "attempt");
  form.append("session_id", sessionId);
  if (rehearsal) form.append("rehearsal", "true");
  return request("/satz/attempts", token, { method: "POST", body: form });
}

// SATZ-007: on-demand unpacking of a correction (POST /satz/explain) — one
// short English paragraph; nothing is recorded server-side.
export async function explainAttempt(
  token: string,
  cardId: string,
  transcript: string,
  corrected: string,
  error: string | null,
  sessionId?: string
): Promise<string> {
  const res = await request<{ explanation: string }>("/satz/explain", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      card_id: cardId,
      transcript,
      corrected,
      error,
      session_id: sessionId ?? null,
    }),
  });
  return res.explanation;
}

// SATZ-008: file a "this verdict seems wrong" flag onto the judgement's
// Langfuse trace (POST /satz/flag) — human ground truth for judge calibration.
export async function flagVerdict(
  token: string,
  traceId: string,
  cardId: string | null,
  transcript: string,
  verdict: string,
  sessionId?: string
): Promise<void> {
  await request<{ flagged: boolean }>("/satz/flag", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      trace_id: traceId,
      card_id: cardId,
      transcript,
      verdict,
      session_id: sessionId ?? null,
    }),
  });
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

// SATZ-010: a wrong gender pick at the pre-record gate — same schedule
// consequence as a reveal (lapse to "due now"), separate endpoint so the
// causes stay distinguishable.
export async function genderMissCard(
  token: string,
  cardId: string
): Promise<{ dueInDays: number }> {
  return request(`/satz/deck/${encodeURIComponent(cardId)}/gender-miss`, token, {
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

// UI-007: hover/tap word-gloss popover — one German word, a short English
// meaning, an example sentence, and whether it's already in the deck.
export type GlossInfo = {
  lemma: string;
  article: string | null;
  gloss: string;
  example: string | null;
  cardId: string | null;
  inDeck: boolean;
  source: "catalog" | "cache" | "fresh" | "function";
  // SATZ-026: false for the closed-class function-word table (articles,
  // pronouns, particles) — an article or pronoun can never become a vocab
  // card, so Glossable hides "add to deck" whenever this is false. Absent
  // (older cached entries) is treated as true.
  glossable?: boolean;
  // SATZ-013: remaining gloss-path adds allowed today (0..3), computed up
  // front so the popover can render the state before any add attempt.
  glossAddsRemaining?: number;
};

// POST /satz/gloss. `context` is the surrounding sentence the word was
// tapped in (helps the backend disambiguate); `sessionId` files the lookup
// under the same OBS-007 practice-sitting Langfuse session as the rest of
// the drill. A 429 (rate-limited) surfaces as a plain Error — callers treat
// any rejection here as a soft failure and just close the popover.
export async function fetchGloss(
  token: string,
  word: string,
  context?: string,
  sessionId?: string
): Promise<GlossInfo> {
  return request("/satz/gloss", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      word,
      context: context ?? null,
      session_id: sessionId ?? null,
    }),
  });
}
