// Word-level helpers for the Sprechen verdict view: anchor each slip quote
// inside the raw transcript (numbered highlights), and diff quote → corrected
// so the repair card marks only the words that actually changed.

export type TranscriptSegment = {
  text: string;
  kind: "plain" | "slip" | "hit";
  // index into verdict.slips (kind="slip") or verdict.hitQuotes (kind="hit");
  // null for plain text.
  index: number | null;
};

export type DiffToken = {
  word: string;
  // ghost = dropped from the learner's words (a true deletion, not a move)
  kind: "kept" | "added" | "ghost";
};

type Token = { raw: string; norm: string; start: number; end: number };

// Judge quotes come back with drifting case/punctuation — compare on a
// stripped lowercase core so "spielen." still matches "spielen".
const normalize = (w: string) =>
  w.toLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");

function tokenize(text: string): Token[] {
  const out: Token[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const norm = normalize(m[0]);
    if (norm)
      out.push({ raw: m[0], norm, start: m.index, end: m.index + m[0].length });
  }
  return out;
}

// Find each quote (slips, then hits) as a contiguous word run in the
// transcript and cut the transcript into plain/slip/hit segments. A quote
// the judge paraphrased (no word-run match) simply gets `matched[i] ===
// false` — the card then shows the quote itself instead of an anchor
// (slips only — hits have no such fallback card, so an unmatched hit is
// silently dropped).
//
// Slips claim their token spans before hits are searched, so a hit quote
// that overlaps an already-claimed (slip) span can never match there — the
// slip wins the overlap, never the other way round.
export function segmentTranscript(
  transcript: string,
  slipQuotes: string[],
  hitQuotes: string[] = []
): { segments: TranscriptSegment[]; matched: boolean[] } {
  const tokens = tokenize(transcript);
  const claimed = new Array<boolean>(tokens.length).fill(false);
  const ranges: { start: number; end: number; kind: "slip" | "hit"; index: number }[] = [];
  const matched = slipQuotes.map(() => false);

  // Locates `quote` as a contiguous run of still-unclaimed tokens, claims
  // those tokens, and records the range under `kind`/`index`. Returns
  // whether a match was found.
  function place(quote: string, kind: "slip" | "hit", index: number): boolean {
    const qWords = tokenize(quote).map((t) => t.norm);
    if (qWords.length === 0) return false;
    for (let i = 0; i + qWords.length <= tokens.length; i++) {
      let ok = true;
      for (let j = 0; j < qWords.length; j++) {
        if (claimed[i + j] || tokens[i + j].norm !== qWords[j]) {
          ok = false;
          break;
        }
      }
      if (ok) {
        for (let j = 0; j < qWords.length; j++) claimed[i + j] = true;
        ranges.push({
          start: tokens[i].start,
          end: tokens[i + qWords.length - 1].end,
          kind,
          index,
        });
        return true;
      }
    }
    return false;
  }

  slipQuotes.forEach((quote, qi) => {
    matched[qi] = place(quote, "slip", qi);
  });
  hitQuotes.forEach((quote, qi) => {
    place(quote, "hit", qi);
  });

  ranges.sort((a, b) => a.start - b.start);
  const segments: TranscriptSegment[] = [];
  let pos = 0;
  for (const r of ranges) {
    if (r.start > pos)
      segments.push({ text: transcript.slice(pos, r.start), kind: "plain", index: null });
    segments.push({ text: transcript.slice(r.start, r.end), kind: r.kind, index: r.index });
    pos = r.end;
  }
  if (pos < transcript.length)
    segments.push({ text: transcript.slice(pos), kind: "plain", index: null });
  return { segments, matched };
}

// LCS word diff of the learner's quote against the minimal repair. Returns
// the corrected sentence as tokens: kept words plain, added words emphasized,
// and true deletions as ghosts. A word that merely moved (e.g. "muss ich" →
// "ich muss") appears as added at its new spot with no ghost — showing a
// deletion mark for a moved word would read as "drop this word".
export function diffWords(quote: string, corrected: string): DiffToken[] {
  const a = tokenize(quote);
  const b = tokenize(corrected);
  const n = a.length;
  const m = b.length;
  // Slip quotes are single clauses — O(n·m) is nothing.
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0)
  );
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] =
        a[i].norm === b[j].norm
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);

  const raw: DiffToken[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i].norm === b[j].norm) {
      raw.push({ word: b[j].raw, kind: "kept" });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push({ word: a[i].raw, kind: "ghost" });
      i++;
    } else {
      raw.push({ word: b[j].raw, kind: "added" });
      j++;
    }
  }
  while (i < n) raw.push({ word: a[i++].raw, kind: "ghost" });
  while (j < m) raw.push({ word: b[j++].raw, kind: "added" });

  const added = new Set(
    raw.filter((t) => t.kind === "added").map((t) => normalize(t.word))
  );
  return raw.filter(
    (t) => t.kind !== "ghost" || !added.has(normalize(t.word))
  );
}
