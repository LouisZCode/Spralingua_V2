// Thin authed client for the /genus backend routes (genus/routes.py) —
// same contract as the other practice clients.
import { HTTP_BASE } from "@/lib/api";
import { InsufficientCoinsError } from "@/lib/coins";
import { UnauthorizedError } from "../satzschmiede/api";
import type { NudgeWord } from "../shared/VocabNudge";

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
  // FLOW-002: present and true only on a give-up reveal — genus has no
  // separate "wrong" reveal card to reuse, so this is the only cue that a
  // correct-shaped reveal was actually a concession, not a right guess.
  gaveUp?: boolean;
  // DATA-009: always "artikel-genus" — Genus tests exactly one taxonomy
  // pattern, unlike the other typed drills. Not consumed on this beat today
  // (the drop stays unrevealed on a genuine miss, and the AnchorCard reveal
  // already carries its own bespoke "why"), but the backend always sends it.
  patternId?: string | null;
};

// Verdict for phase="phrase" (typed production). Recognized phrase shapes
// are graded deterministically; any other sentence containing the noun
// (≤140 chars) goes to the one-shot gender judge, which checks ONLY that
// the noun's gender is used correctly.
export type PhraseVerdict = {
  correct: boolean;
  // Deterministic misses: the gold phrase for the attempted form family.
  // Judge misses: the learner's own sentence with the gender fixed.
  expected: string | null;
  article: Article;
  // What went wrong: wrong article form ("article"), wrong adjective ending
  // ("adjective"), misspelled noun ("noun"), judge-found gender slip
  // ("gender"), or input that isn't a gradable attempt ("unrecognized" —
  // guidance only, never scored, the item stays live).
  kind: "match" | "noun" | "article" | "adjective" | "gender" | "unrecognized";
  note: string | null;
  // Index of the offending token in the TYPED answer — the frontend marks
  // exactly that word red (no strikethrough). null when it's not one token.
  wrongIndex: number | null;
  // GRAM-009 / DATA-009, narrowed by genusfix: "artikel-genus" only when
  // `kind` is "article" or "gender" — i.e. this particular miss WAS a
  // gender slip — so the wrong-answer FeedbackCard offers the shared
  // "Warum?" disclosure only for a gender miss, not a declension/adjective
  // slip (which isn't about gender) or a match/unrecognized result.
  patternId?: string | null;
};

// One deck word the vocab nudge picked for the production beat: the word
// (nouns wear their article) plus a short German example using both it and
// the target noun. The pill shows only the count; clicking reveals these.
// Re-exported from the shared VocabNudge component so existing imports
// (e.g. Genus.tsx) keep working.
export type { NudgeWord };

// The intro cheat sheet: ending labels per article, in rules.yaml order.
export type EndingSheet = Record<Article, string[]>;

// A selectable curated noun pool (the intro chips). The deck half of a
// round is always the learner's own nouns, whatever pool is chosen.
export type GenusPool = { id: string; title: string };

export type GenusMeta = { endings: EndingSheet; pools: GenusPool[] };

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

export async function fetchRound(
  token: string,
  pool?: string
): Promise<GenusItem[]> {
  const query = pool ? `?pool=${encodeURIComponent(pool)}` : "";
  const data = await request<{ items: GenusItem[] }>(
    `/genus/round${query}`,
    token
  );
  return data.items;
}

export async function fetchMeta(token: string): Promise<GenusMeta> {
  return request<GenusMeta>("/genus/rules", token);
}

export async function submitArticle(
  token: string,
  itemId: string,
  article: Article,
  // OBS-007: the practice-sitting id (minted by the Genus shell).
  sessionId: string,
  // genusfix BLOCKER 1: true for every drag after the first one on this
  // same item instance — the backend skips the ledger for a retry so a
  // drag-until-correct sequence can't open/credit the pattern more than
  // once. Defaults false so an existing call site that hasn't been updated
  // to thread it through still behaves like a (safe, if imprecise) fresh
  // attempt rather than failing to compile.
  retry: boolean = false
): Promise<ArticleVerdict> {
  return request("/genus/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: itemId,
      phase: "article",
      answer: article,
      session_id: sessionId,
      retry,
    }),
  });
}

// FLOW-002: the deliberate "give up" escape (Flow mode only, the drag beat
// Flow deals) — same endpoint/phase, `give_up: true` skips the drag check
// and grades a real, distinguishable miss while still revealing the article.
export async function giveUpArticle(
  token: string,
  itemId: string,
  sessionId: string
): Promise<ArticleVerdict> {
  return request("/genus/attempts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: itemId,
      phase: "article",
      answer: "",
      give_up: true,
      session_id: sessionId,
    }),
  });
}

// Decorative endpoint: the backend answers {words: []} on any internal
// failure, so callers only ever handle the happy shape (plus 401).
export async function fetchNudge(
  token: string,
  itemId: string,
  sessionId: string
): Promise<NudgeWord[]> {
  const data = await request<{ words: NudgeWord[] }>("/genus/nudge", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId, session_id: sessionId }),
  });
  return data.words;
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
