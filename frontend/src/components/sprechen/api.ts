// Thin authed client for the /sprechen backend routes (sprechen/routes.py) —
// same Bearer-replay + 401-signout contract as the other practice clients.
import { HTTP_BASE } from "@/lib/api";
import { InsufficientCoinsError } from "@/lib/coins";
import { UnauthorizedError } from "../satzschmiede/api";
import type { NudgeWord } from "../shared/VocabNudge";

export type { NudgeWord };

// One constrained speaking task as the round serves it. The judge rubric
// (`forces`) stays server-side — the learner sees only the task.
export type SpokenTask = {
  id: string;
  title: string; // short chip: "weil × 2"
  prompt: string; // the constrained instruction, English
};

export type Slip = {
  quote: string; // the learner's own words where the structure broke
  corrected: string; // minimal repair, in German
  note: string; // ≤10-word English line naming the broken rule
};

// Constraint compliance + target-structure verdict. The raw transcript is
// part of the format — seeing what you actually said is the exercise.
export type SprechenVerdict = {
  transcript: string;
  passed: boolean; // constraint met AND no slips
  constraintMet: boolean;
  constraintNote: string | null;
  hits: number; // correct productions of the target structure
  // SPRECH-001: verbatim spans where the structure fired correctly, same
  // anchoring contract as slips. Optional — absent on an older backend
  // response, callers should default to [].
  hitQuotes?: string[];
  slips: Slip[];
  // FLOW-002: present and true only on a /sprechen/give-up response — the
  // trainer uses it to show a modest "gave up" state instead of the (empty,
  // unjudged) transcript UI a real attempt would render.
  gaveUp?: boolean;
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

// VARY-001: a round as the backend serves it — `cycleReset` is true when the
// server's seen-pool was exhausted and reset for this pick, absent only
// against an old backend that predates variety tracking.
type RoundResponse = { tasks: SpokenTask[]; cycleReset?: boolean };

export async function fetchRound(
  token: string,
  // VARY-001: task ids already served this pool cycle (localStorage-backed,
  // see Sprechen.tsx) — omit or pass empty for the old stateless draw.
  seenTasks?: string[]
): Promise<RoundResponse> {
  const qs =
    seenTasks && seenTasks.length > 0
      ? `?seenTasks=${encodeURIComponent(seenTasks.join(","))}`
      : "";
  return request<RoundResponse>(`/sprechen/round${qs}`, token);
}

// Decorative endpoint: the backend answers {words: []} on any internal
// failure, so callers only ever handle the happy shape (plus 401).
export async function fetchNudge(
  token: string,
  taskId: string,
  sessionId: string
): Promise<NudgeWord[]> {
  const data = await request<{ words: NudgeWord[] }>(
    "/sprechen/nudge",
    token,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId, session_id: sessionId }),
    }
  );
  return data.words;
}

export async function submitAttempt(
  token: string,
  taskId: string,
  audio: Blob,
  // OBS-007: the practice-sitting id (minted by the Sprechen shell on the
  // first attempt) — the backend files the attempt's Langfuse trace under it.
  sessionId: string
): Promise<SprechenVerdict> {
  // No Content-Type header: the browser sets the multipart boundary itself.
  const form = new FormData();
  form.append("task_id", taskId);
  form.append("audio", audio, "attempt");
  form.append("session_id", sessionId);
  return request("/sprechen/attempts", token, { method: "POST", body: form });
}

// FLOW-002: the deliberate "give up" escape (Flow mode only). /attempts
// takes a multipart audio upload, so a flag doesn't fit here — this hits a
// tiny sibling JSON route instead, with no recording involved.
export async function giveUp(
  token: string,
  taskId: string,
  sessionId: string
): Promise<SprechenVerdict> {
  return request("/sprechen/give-up", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId, session_id: sessionId }),
  });
}
