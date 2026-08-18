// Thin authed client for the /interview backend routes (interview/routes.py,
// INTV-003 slice 2) — same Bearer-replay + 401-signout + detail-surface
// contract as the other practice clients (see sprechen/api.ts).
//
// One deliberate deviation from every sibling api.ts in this codebase:
// interview/routes.py builds its response dicts with snake_case keys
// (n_chunks, duration_s, got_right, coach_note, german_version, …) instead of
// the camelCase every other drill router hand-writes (satz/routes.py's
// dueInDays, newAllowance, …). That's a backend inconsistency this slice
// can't fix (frontend-only scope) — so every fetch function below maps the
// raw snake_case JSON into a camelCase type at the boundary, same job any
// api.ts would do facing a third-party API.
import { HTTP_BASE } from "@/lib/api";
import { UnauthorizedError } from "../satzschmiede/api";

export { UnauthorizedError };

// ─── Items ──────────────────────────────────────────────────────────────

export type InterviewItemSummary = {
  id: string;
  title: string;
  company: string;
  // "" means the workbench dir name never yielded a person — hide the line.
  interviewer: string;
  level: string | null;
  language: string;
  nChunks: number;
  createdAt: string | null;
};

export type Segment = { t0: number; t1: number; text: string };

export type BriefQuestion = { text: string; source: string };
export type Brief = {
  shape: string;
  summary: string;
  question: BriefQuestion | null;
  goals: string[];
};

export type ChunkKind = "question" | "statement" | "monologue" | string;

export type Chunk = {
  id: string;
  position: number;
  transcript: string;
  segments: Segment[];
  brief: Brief | null;
  durationS: number | null;
  kind: ChunkKind;
};

export type InterviewItem = {
  id: string;
  title: string;
  company: string;
  interviewer: string;
  level: string | null;
  language: string;
  createdAt: string | null;
  chunks: Chunk[];
};

// ─── Round 1 (listen & retell) ─────────────────────────────────────────

export type ComprehensionVerdict = {
  passed: boolean;
  gotRight: string;
  nudge: string;
};

export type ComprehensionResult =
  | { kind: "verdict"; transcript: string; comprehension: ComprehensionVerdict }
  | { kind: "judge_error"; transcript: string; comprehensionError: string }
  | { kind: "no_speech" };

// ─── Round 2 (read & answer) ────────────────────────────────────────────

export type GrammarSlip = { quote: string; corrected: string; note: string };
export type GrammarVerdict = {
  ok: boolean;
  slips: GrammarSlip[];
  coachNote: string;
};

export type IdiomSuggestion = { original: string; germanWay: string; why: string };
export type IdiomVerdict = {
  germanVersion: string;
  suggestions: IdiomSuggestion[];
  coachNote: string;
};

export type GoalCoverageItem = { goal: string; covered: boolean; note: string };

export type AnswerResult =
  | {
      kind: "answered";
      transcript: string;
      grammar: GrammarVerdict | null;
      grammarError: string | null;
      idiom: IdiomVerdict | null;
      idiomError: string | null;
      goalCoverage: GoalCoverageItem[] | null;
      goalCoverageError: string | null;
      at: string;
    }
  | { kind: "no_speech" };

// ─── raw (snake_case, as routes.py actually serializes) shapes ──────────

type RawItemSummary = {
  id: string;
  title: string;
  company: string;
  interviewer: string;
  level: string | null;
  language: string;
  n_chunks: number;
  created_at: string | null;
};

type RawChunk = {
  id: string;
  position: number;
  transcript: string;
  segments: Segment[];
  brief: Brief | null;
  duration_s: number | null;
  kind: ChunkKind;
};

type RawItem = {
  id: string;
  title: string;
  company: string;
  interviewer: string;
  level: string | null;
  language: string;
  created_at: string | null;
  chunks: RawChunk[];
};

type RawComprehensionResponse =
  | { transcript: string; comprehension: { passed: boolean; got_right: string; nudge: string } }
  | { transcript: string; comprehension_error: string }
  | { transcript: ""; error: "no_speech" };

type RawAnswerResponse =
  | {
      transcript: string;
      grammar: { ok: boolean; slips: GrammarSlip[]; coach_note: string } | null;
      idiom:
        | {
            german_version: string;
            suggestions: { original: string; german_way: string; why: string }[];
            coach_note: string;
          }
        | null;
      at: string;
      grammar_error?: string;
      idiom_error?: string;
      goal_coverage?: GoalCoverageItem[];
      goal_coverage_error?: string;
    }
  | { transcript: ""; error: "no_speech" };

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

export async function fetchItems(token: string): Promise<InterviewItemSummary[]> {
  const raw = await request<RawItemSummary[]>("/interview/items", token);
  return raw.map((r) => ({
    id: r.id,
    title: r.title,
    company: r.company,
    interviewer: r.interviewer,
    level: r.level,
    language: r.language,
    nChunks: r.n_chunks,
    createdAt: r.created_at,
  }));
}

export async function fetchItem(token: string, itemId: string): Promise<InterviewItem> {
  const r = await request<RawItem>(`/interview/items/${encodeURIComponent(itemId)}`, token);
  return {
    id: r.id,
    title: r.title,
    company: r.company,
    interviewer: r.interviewer,
    level: r.level,
    language: r.language,
    createdAt: r.created_at,
    chunks: r.chunks
      .slice()
      .sort((a, b) => a.position - b.position)
      .map((c) => ({
        id: c.id,
        position: c.position,
        transcript: c.transcript,
        segments: c.segments ?? [],
        brief: c.brief,
        durationS: c.duration_s,
        kind: c.kind,
      })),
  };
}

// GET /interview/audio/{chunk_id}?redirect=0 -> {url}. The session JWT rides
// an Authorization header, which an <audio> element can't send — so this is
// fetched with the header and the resulting (already-presigned, ~15-min)
// URL is handed straight to the media element, whose load isn't CORS-gated.
// Fetch fresh on every (re)start of a chunk rather than caching it.
export async function fetchAudioUrl(token: string, chunkId: string): Promise<string> {
  const r = await request<{ url: string }>(
    `/interview/audio/${encodeURIComponent(chunkId)}?redirect=0`,
    token
  );
  return r.url;
}

export async function submitComprehension(
  token: string,
  chunkId: string,
  audio: Blob
): Promise<ComprehensionResult> {
  const form = new FormData();
  form.append("audio", audio, "retell");
  const r = await request<RawComprehensionResponse>(
    `/interview/comprehension/${encodeURIComponent(chunkId)}`,
    token,
    { method: "POST", body: form }
  );
  if ("error" in r) {
    return { kind: "no_speech" };
  }
  if ("comprehension_error" in r) {
    return { kind: "judge_error", transcript: r.transcript, comprehensionError: r.comprehension_error };
  }
  return {
    kind: "verdict",
    transcript: r.transcript,
    comprehension: {
      passed: r.comprehension.passed,
      gotRight: r.comprehension.got_right,
      nudge: r.comprehension.nudge,
    },
  };
}

export async function submitAnswer(
  token: string,
  chunkId: string,
  audio: Blob,
  // OBS-007: the practice-sitting id (minted by Interview.tsx on the first
  // round-2 attempt) — the backend files the ledger harvest's Langfuse
  // grouping under it. Round 1 (/comprehension) takes no such field.
  sessionId: string
): Promise<AnswerResult> {
  const form = new FormData();
  form.append("audio", audio, "answer");
  form.append("session_id", sessionId);
  const r = await request<RawAnswerResponse>(
    `/interview/answer/${encodeURIComponent(chunkId)}`,
    token,
    { method: "POST", body: form }
  );
  if ("error" in r) {
    return { kind: "no_speech" };
  }
  return {
    kind: "answered",
    transcript: r.transcript,
    grammar: r.grammar
      ? { ok: r.grammar.ok, slips: r.grammar.slips, coachNote: r.grammar.coach_note }
      : null,
    grammarError: r.grammar_error ?? null,
    idiom: r.idiom
      ? {
          germanVersion: r.idiom.german_version,
          suggestions: r.idiom.suggestions.map((s) => ({
            original: s.original,
            germanWay: s.german_way,
            why: s.why,
          })),
          coachNote: r.idiom.coach_note,
        }
      : null,
    idiomError: r.idiom_error ?? null,
    goalCoverage: r.goal_coverage ?? null,
    goalCoverageError: r.goal_coverage_error ?? null,
    at: r.at,
  };
}
