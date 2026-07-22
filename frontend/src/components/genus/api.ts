// Thin authed client for the /genus backend routes (genus/routes.py) —
// same contract as the other practice clients.
import { HTTP_BASE } from "@/lib/api";
import { UnauthorizedError } from "../satzschmiede/api";

export type Article = "der" | "die" | "das";

// One item as the round serves it. The article, rule, and anchor stay
// server-side until the verdict — the drag IS the question.
export type GenusItem = {
  id: string;
  noun: string;
  gloss: string;
  adjective: string; // base form for the production beat (neu → "eine neue …")
};

// Verdict for phase="article" (the drag). On a correct drop the payload
// carries everything the anchor card needs; on a wrong drop it carries
// nothing that would reveal the answer — only whether a trap just fired.
export type ArticleVerdict = {
  correct: boolean;
  article?: Article;
  // The part of the noun to keep bold+colored ("ung" of Wohnung, "Ge" of
  // Gefühl). null for traps and pattern-free words → whole-word tint.
  segment?: { kind: "suffix" | "prefix"; text: string } | null;
  anchor?: string | null; // the character scene line (rule words only)
  reliability?: string | null; // "~100%" — shown with the anchor
  trap?: boolean; // correct drop on a trap word → note carries the why
  trapped?: boolean; // wrong drop that fell FOR the trap ("Falle!")
  note?: string | null;
};

// Verdict for phase="phrase" (typed production). Accepts definite or
// indefinite phrases, bare or wrapped in a whitelisted carrier sentence
// ("Ich liebe …" forces the accusative and is graded in it).
export type PhraseVerdict = {
  correct: boolean;
  expected: string | null; // the gold phrase for the attempted form family
  article: Article;
  // What went wrong: wrong article form ("article"), wrong adjective ending
  // ("adjective"), misspelled noun ("noun"), not three phrase words
  // ("shape"), or an opener the grader can't read ("unrecognized" —
  // guidance only, never scored, the item stays live).
  kind: "match" | "shape" | "noun" | "article" | "adjective" | "unrecognized";
  note: string | null;
  // Index of the offending token in the TYPED answer — the frontend marks
  // exactly that word red (no strikethrough). null when it's not one token.
  wrongIndex: number | null;
};

// The intro cheat sheet: ending labels per article, in rules.yaml order.
export type EndingSheet = Record<Article, string[]>;

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

export async function fetchRound(token: string): Promise<GenusItem[]> {
  const data = await request<{ items: GenusItem[] }>("/genus/round", token);
  return data.items;
}

export async function fetchEndings(token: string): Promise<EndingSheet> {
  const data = await request<{ endings: EndingSheet }>("/genus/rules", token);
  return data.endings;
}

export async function submitArticle(
  token: string,
  itemId: string,
  article: Article,
  // OBS-007: the practice-sitting id (minted by the Genus shell).
  sessionId: string
): Promise<ArticleVerdict> {
  return request("/genus/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: itemId,
      phase: "article",
      answer: article,
      session_id: sessionId,
    }),
  });
}

export async function submitPhrase(
  token: string,
  itemId: string,
  answer: string,
  sessionId: string
): Promise<PhraseVerdict> {
  return request("/genus/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: itemId,
      phase: "phrase",
      answer,
      session_id: sessionId,
    }),
  });
}
