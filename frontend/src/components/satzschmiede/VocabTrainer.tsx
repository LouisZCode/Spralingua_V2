"use client";

import { useEffect, useRef, useState } from "react";
import { WordRejectedError, type AttemptResult } from "./api";
import type { DeckCard } from "./deck";

type Verdict = "correct" | "close" | "revealed";

// No verdict text anywhere — the tint (green/red/gold) plus a ✓/✕ mark carry
// the outcome, so the back stays scannable: the word first, the attempt after.
const VERDICT_STYLE: Record<Verdict, { faceBox: string; tone: string }> = {
  correct: {
    faceBox: "border-success bg-success-soft",
    tone: "text-success",
  },
  close: {
    faceBox: "border-flag-red bg-flag-red-soft",
    tone: "text-flag-red-deep",
  },
  revealed: {
    faceBox: "border-flag-gold bg-flag-gold-soft",
    tone: "text-flag-gold-deep",
  },
};

// Gender colour-coding — the classic learning mnemonic: der=red, die=blue,
// das=green. Applied to the article only, never to the noun itself. The
// palette has no blue (flag colours), so `die` gets a functional one-off.
const ARTICLE_COLOR: Record<string, string> = {
  der: "text-flag-red",
  die: "text-[#2563eb]",
  das: "text-success",
};

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;
const goldShadow = {
  ["--shadow-color"]: "var(--color-flag-gold-deep)",
} as React.CSSProperties;

// One sentence is all we judge — auto-stop keeps a forgotten open mic from
// uploading minutes of audio (the backend caps bytes for the same reason).
// Manual recording now, so the cap is generous: thinking happens with the
// mic hot, not before it.
const MAX_RECORD_SECONDS = 45;

// How many never-practiced words one session introduces — the classic SRS
// drip. The rest wait in the pool for tomorrow's queue (or the "+ practice
// more" button on the done panel).
const NEW_PER_SESSION = 15;

// Chrome/Firefox record opus-in-webm, Safari aac-in-mp4 — Deepgram takes both
// as-is, so we just pick the first container the browser supports.
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// Today's work: everything due plus a capped drip of new words, shuffled so
// packs don't replay in authoring order. `exclude` holds cards already
// finished this session — the deck's srs is a fetch-time snapshot, so the
// server can't tell us that.
function buildQueue(deck: DeckCard[], exclude: Set<string>): string[] {
  const pool = deck.filter((c) => !exclude.has(c.id));
  const due = pool.filter((c) => c.srs.status === "due").map((c) => c.id);
  const fresh = pool.filter((c) => c.srs.status === "new").map((c) => c.id);
  return shuffle([...due, ...shuffle(fresh).slice(0, NEW_PER_SESSION)]);
}

export default function VocabTrainer({
  deck,
  onRemove,
  onAttempt,
  onReveal,
  onExplain,
  sessionPrefix = "satz",
}: {
  deck: DeckCard[];
  // Drop the current card from the pool; the parent refetches the deck and
  // this prop shrinks — the queue/index below adjust to the missing id.
  onRemove: (cardId: string) => Promise<void>;
  // Judge one recorded sentence (POST /satz/attempts via the parent, which
  // owns the token). Resolves with the examiner's verdict. `sessionId` is the
  // practice-sitting id (OBS-007) — minted here, threaded to the backend.
  onAttempt: (
    cardId: string,
    audio: Blob,
    sessionId: string
  ) => Promise<AttemptResult>;
  // Record a practice-mode peek as a lapse (fire-and-forget via the parent).
  onReveal: (cardId: string) => void;
  // SATZ-007: unpack the correction on demand (POST /satz/explain via the
  // parent). Optional — the button only renders when the parent wires it.
  onExplain?: (
    cardId: string,
    transcript: string,
    corrected: string,
    error: string | null,
    sessionId?: string
  ) => Promise<string>;
  // Langfuse practice-session id prefix (OBS-007). The Verbformen mode
  // (GRAM-002 Exercise C) reuses this trainer on a verb-only sub-deck and
  // passes "vf" so its sittings group apart from regular Satzschmiede ones.
  sessionPrefix?: string;
}) {
  // Two ways to face the pool: "practice" works today's queue (due + a drip
  // of new, shuffled — green pops a card, anything else recycles it); browse
  // is the old free walk over everything, schedule untouched by peeks.
  const [browsing, setBrowsing] = useState(false);
  const [queue, setQueue] = useState<string[]>(() =>
    buildQueue(deck, new Set())
  );
  const [index, setIndex] = useState(0); // browse-mode position

  const [revealed, setRevealed] = useState(false); // used the hint → a miss
  const [flipped, setFlipped] = useState(false); // showing the answer side
  const [removing, setRemoving] = useState(false);

  const [recording, setRecording] = useState(false);
  const [checking, setChecking] = useState(false); // clip uploaded, verdict pending
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<AttemptResult | null>(null);
  const [attemptError, setAttemptError] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<string | null>(null);
  const [explaining, setExplaining] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  // Set when the clip must NOT be submitted (unmount mid-recording).
  const discardRef = useRef(false);
  // One Langfuse Session per practice sitting (OBS-007): minted lazily on the
  // first attempt (pure browsing never creates a session), then held for the
  // whole mount — mode toggles, retries, and top-ups all share it. Unmount
  // (leaving the route) retires it with the ref.
  const practiceSessionRef = useRef<string | null>(null);
  // Cards finished (green) this session — the deck's srs snapshot still calls
  // them due/new, so re-queueing has to exclude them by hand.
  const doneRef = useRef<Set<string>>(new Set());

  const total = deck.length;
  const byId = new Map(deck.map((c) => [c.id, c] as const));
  // The deck can shrink underneath us (a removal refetch) — drop dead ids
  // from the queue and clamp the browse index instead of crashing.
  const activeQueue = queue.filter((id) => byId.has(id));
  const safeIndex = Math.min(index, total - 1);
  const card = browsing ? deck[safeIndex] : byId.get(activeQueue[0]);

  // Card navigation stays locked while a recording or check is in flight, so
  // the verdict that comes back always belongs to the card on screen.
  const busy = recording || checking;

  // The word is what the card tests: wordOk alone decides green vs red. A
  // grammar slip elsewhere in the sentence stays green and shows up as a
  // separate grammar note instead.
  const verdict: Verdict = revealed
    ? "revealed"
    : result?.wordOk
      ? "correct"
      : "close";
  const v = VERDICT_STYLE[verdict];
  const grammarNote = result != null && result.wordOk && !result.grammarOk;

  // Nouns hide the article, reflexive verbs hide `sich` — the same move: a
  // grammatical lead stripped from the clue and restored here on the answer so
  // the learner sees what they were meant to recall.
  const lead = card?.article ?? (card?.reflexive ? "sich" : undefined);
  // The coloured article already encodes the gender — drop the note's
  // redundant "feminine ·" prefix (phrase register notes pass untouched).
  const note = card?.note?.replace(/^(masculine|feminine|neuter)\s*·\s*/, "");

  const dueLeft = activeQueue.filter(
    (id) => byId.get(id)?.srs.status === "due"
  ).length;
  const newLeft = activeQueue.filter(
    (id) => byId.get(id)?.srs.status === "new"
  ).length;
  const practiceLabel =
    [
      dueLeft > 0 ? `${dueLeft} due` : null,
      newLeft > 0 ? `${newLeft} new` : null,
    ]
      .filter(Boolean)
      .join(" · ") || `${activeQueue.length} left`;

  // When the flipped card comes back, per the scheduler: a browse-mode peek
  // costs nothing, so it shows no line.
  const dueDays = result
    ? result.dueInDays
    : revealed && !browsing
      ? 0
      : null;
  const scheduleLine =
    dueDays === null
      ? " "
      : dueDays <= 0
        ? "Again today"
        : dueDays === 1
          ? "Back tomorrow"
          : `Back in ${dueDays} days`;

  // Recording timer + auto-stop.
  useEffect(() => {
    if (!recording) return;
    setElapsed(0);
    const started = Date.now();
    const tick = setInterval(() => {
      const s = Math.floor((Date.now() - started) / 1000);
      setElapsed(s);
      if (s >= MAX_RECORD_SECONDS) stopRecording();
    }, 250);
    return () => clearInterval(tick);
  }, [recording]);

  // Unmount mid-recording: kill the mic without submitting the partial clip.
  useEffect(
    () => () => {
      discardRef.current = true;
      if (recorderRef.current?.state === "recording") {
        recorderRef.current.stop();
      }
    },
    []
  );

  async function startRecording() {
    if (busy || flipped || !card) return;
    setAttemptError(null);
    setResult(null);
    setExplanation(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setAttemptError(
        "Microphone blocked — allow mic access in your browser and try again."
      );
      return;
    }
    // MediaRecorder construction can throw on older Safari / restrictive
    // WebViews — outside a try/catch that leaves the mic stream we just
    // acquired orphaned (hot, unreachable by any cleanup).
    try {
      const mime = MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m));
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        if (discardRef.current) return;
        void check(new Blob(chunks, { type: rec.mimeType || "audio/webm" }));
      };
      discardRef.current = false;
      recorderRef.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      setAttemptError(
        "Microphone blocked — allow mic access in your browser and try again."
      );
    }
  }

  function stopRecording() {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
    }
  }

  // UI-006: a messed-up take — stop and throw the clip away; onstop sees
  // discardRef and never submits. startRecording resets the flag.
  function discardRecording() {
    if (recorderRef.current?.state === "recording") {
      discardRef.current = true;
      recorderRef.current.stop();
    }
  }

  async function check(audio: Blob) {
    if (!card) return;
    // Mode prefix ("satz-" / "vf-") + 32-hex tail mirrors the backend's
    // uuid4().hex session ids while staying recognizable in Langfuse.
    practiceSessionRef.current ??=
      sessionPrefix + "-" + crypto.randomUUID().replace(/-/g, "");
    setChecking(true);
    try {
      const res = await onAttempt(card.id, audio, practiceSessionRef.current);
      setResult(res);
      setFlipped(true);
    } catch (err) {
      if (err instanceof WordRejectedError) {
        // Learner-facing sentence from the backend ("We couldn't hear
        // anything…") — show it verbatim.
        setAttemptError(err.message);
      } else {
        setAttemptError("The check hiccuped — try again in a moment.");
      }
    } finally {
      setChecking(false);
    }
  }

  // SATZ-007: one on-demand call; the paragraph replaces the button in place.
  async function handleExplain() {
    if (!card || !result?.corrected || !onExplain || explaining) return;
    setExplaining(true);
    try {
      setExplanation(
        await onExplain(
          card.id,
          result.transcript,
          result.corrected,
          result.error ?? null,
          practiceSessionRef.current ?? undefined
        )
      );
    } catch {
      setExplanation("Couldn't fetch the explanation — try again in a moment.");
    } finally {
      setExplaining(false);
    }
  }

  // Reset the per-card scratch state on every move.
  function resetScratch() {
    setRevealed(false);
    setFlipped(false);
    setResult(null);
    setExplanation(null);
    setAttemptError(null);
  }

  // Browse: step through the whole pool, wrapping so it never dead-ends.
  // Practice: rotate the queue — skipping is allowed, escaping is not (the
  // card stays in today's round).
  function move(dir: 1 | -1) {
    if (busy) return;
    if (browsing) {
      setIndex((i) => (Math.min(i, total - 1) + dir + total) % total);
    } else {
      setQueue((q) => {
        const act = q.filter((id) => byId.has(id));
        if (act.length < 2) return act;
        return dir === 1
          ? [...act.slice(1), act[0]]
          : [act[act.length - 1], ...act.slice(0, -1)];
      });
    }
    resetScratch();
  }

  // The forward move, with meaning: in practice a green answer pops the card
  // from today's queue, anything else (red, reveal, plain skip) recycles it
  // to the back for another go. Browse just walks on.
  function handleNext() {
    if (busy) return;
    if (browsing) {
      move(1);
      return;
    }
    if (!card) return;
    const passed = !revealed && result?.wordOk === true;
    if (passed) doneRef.current.add(card.id);
    setQueue((q) => {
      const act = q.filter((id) => byId.has(id));
      if (act.length === 0) return act;
      const [head, ...rest] = act;
      return passed ? rest : [...rest, head];
    });
    resetScratch();
  }

  // SATZ-003: retry the same card right away instead of waiting for the
  // recycle — corrective feedback works best when the learner re-produces
  // the correct form while it's still on their mind. The failed attempt
  // already recorded its miss (interval reset to 0, still due), so a green
  // retry simply climbs the ladder from there — no schedule state to undo.
  // Clearing the scratch un-flips the card; the card stays at the queue
  // head, ready for another tap of the Record button.
  function tryAgain() {
    if (busy) return;
    resetScratch();
  }

  // Top the queue up with the next drip of new words (done-panel button).
  function startMore() {
    setQueue(buildQueue(deck, doneRef.current));
    resetScratch();
  }

  function toggleMode() {
    if (busy) return;
    setBrowsing((b) => !b);
    resetScratch();
  }

  async function handleRemove() {
    if (removing || busy || !card) return;
    setRemoving(true);
    try {
      await onRemove(card.id);
      // The shrunken deck arrives via props; reset the scratch state so the
      // card that slides into this slot starts fresh.
      resetScratch();
    } finally {
      setRemoving(false);
    }
  }

  if (!card) {
    // Practice queue exhausted (browse always has a card — the parent only
    // mounts the trainer on a non-empty pool). New words beyond today's drip
    // can be pulled in; otherwise the pool is resting.
    const moreNew = deck.filter(
      (c) => c.srs.status === "new" && !doneRef.current.has(c.id)
    ).length;
    return (
      <div className="mx-auto w-full max-w-xl text-center">
        <h2 className="font-display text-[clamp(26px,5vw,36px)] font-black leading-tight tracking-tight text-ink">
          {doneRef.current.size > 0
            ? "Alles geschmiedet!"
            : "Nothing due today."}
        </h2>
        <p className="mx-auto mt-3 max-w-[380px] font-body text-[15px] leading-relaxed text-ink-soft">
          {moreNew > 0
            ? `Today's round is done — ${moreNew} new ${
                moreNew === 1 ? "word is" : "words are"
              } still waiting in your pool.`
            : "Your words are resting — they'll come back when it's time to forge them again."}
        </p>
        {moreNew > 0 && (
          <button
            type="button"
            onClick={startMore}
            className="btn-3d mt-7 inline-flex items-center gap-2 rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3.5 font-display text-[15px] font-black uppercase tracking-[0.16em] text-white"
            style={redShadow}
          >
            + Practice {Math.min(moreNew, NEW_PER_SESSION)} more
          </button>
        )}
        <div className="mt-6">
          <button
            type="button"
            onClick={() => setBrowsing(true)}
            className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
          >
            Browse all {total} words →
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-xl">
      {/* Progress + navigation. Practice counts down today's queue; browse
          keeps the old free walk over the whole pool. */}
      <div className="mb-2 flex items-center justify-between">
        <button
          type="button"
          onClick={() => move(-1)}
          disabled={busy}
          className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red disabled:opacity-40"
        >
          ← Prev
        </button>
        <span className="font-body text-[11px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
          {browsing ? `Word ${safeIndex + 1} of ${total}` : practiceLabel}
        </span>
        <button
          type="button"
          onClick={handleNext}
          disabled={busy}
          className="font-body text-[12px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red disabled:opacity-40"
        >
          Next →
        </button>
      </div>

      {/* Mode toggle — browse is the escape hatch, never the default. */}
      <div className="mb-4 text-center">
        <button
          type="button"
          onClick={toggleMode}
          disabled={busy}
          className="font-body text-[10px] font-bold uppercase tracking-[0.2em] text-ink-faint transition-colors hover:text-flag-red disabled:opacity-40"
        >
          {browsing ? "← Back to practice" : `Browse all ${total} →`}
        </button>
      </div>

      {/* The card — word only, no type badge: capitalization is the signal
          (nouns capitalized, verbs/phrases not). The one exception is a
          verb's spoken-past sibling, which needs its PAST chip so the
          learner knows which tense to produce. Flips in place. */}
      {/* SATZ-004: no fixed height — the grid-stacked faces (globals.css)
          size the card to whatever the verdict produced. */}
      <div className="flip-scene">
        <div className={`flip-card ${flipped ? "is-flipped" : ""}`}>
          {/* ── Front: nothing but the word to produce ───────────────── */}
          <div className="flip-face items-center justify-center rounded-[28px] border-[3px] border-ink bg-white p-7 text-center shadow-[0_6px_0_var(--color-ink)]">
            <h2 className="font-display text-[clamp(30px,6vw,44px)] font-black leading-[1.05] tracking-tight text-ink">
              {card.target}
            </h2>
            {card.tense === "past" && (
              <span className="mt-4 rounded-full border-[2px] border-ink px-3.5 py-1 font-body text-[10px] font-black uppercase tracking-[0.24em] text-ink">
                past
              </span>
            )}
          </div>

          {/* ── Back, learn-first split: the word (zone 1), then the
                attempt (zone 2). No verdict headline, no scrolling — the
                tint and ✓/✕ say it, the corrected sentence teaches it. ── */}
          <div
            className={`flip-face flip-back rounded-[28px] border-[3px] p-7 shadow-[0_6px_0_var(--color-ink)] ${v.faceBox}`}
          >
            <div className="flex min-h-0 flex-1 flex-col">
              {/* Zone 1, a centered stack: the word (article colour-coded by
                  gender), the translation under it, the plural a step lower. */}
              <div className="text-center">
                <p className="font-display text-[28px] font-black leading-tight text-ink">
                  {/* The past sibling's answer IS the spoken form — pronoun
                      included for reflexives ("hat sich gefreut"), so no
                      lead needed. */}
                  {card.tense === "past" && card.tenseForm ? (
                    card.tenseForm
                  ) : (
                    <>
                      {lead && (
                        <span
                          className={
                            card.article
                              ? ARTICLE_COLOR[card.article]
                              : "text-ink"
                          }
                        >
                          {lead}{" "}
                        </span>
                      )}
                      {card.target}
                    </>
                  )}
                </p>
                <p className="mt-1 font-body text-[15px] font-semibold text-ink-soft">
                  {card.gloss}
                </p>
                {note && (
                  <p className="mt-2 font-body text-[13px] font-semibold text-ink-muted">
                    {note}
                  </p>
                )}
              </div>

              {/* Zone 2: what you said → the fix (or the example if you
                  peeked instead of attempting). */}
              <div className="mt-4 flex-1 border-t-[2px] border-ink/20 pt-3">
                <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
                  {result ? "Your attempt" : "Natural example"}
                </p>
                {result ? (
                  <>
                    <p className="mt-2 font-body text-[17px] leading-snug text-ink">
                      <span className={`mr-1.5 font-black ${v.tone}`}>
                        {verdict === "correct" ? "✓" : "✕"}
                      </span>
                      „{result.transcript}“
                    </p>
                    {/* Word error (red): correction + optional hint inline. */}
                    {!result.wordOk && result.corrected && (
                      <p className="mt-3.5 font-body text-[17px] font-bold leading-snug text-ink">
                        <span className={`mr-1.5 ${v.tone}`}>→</span>
                        {result.corrected}
                      </p>
                    )}
                    {!result.wordOk && result.error && (
                      <p className="mt-2 font-body text-[13px] leading-snug text-ink-soft">
                        {result.error}
                      </p>
                    )}
                    {/* Word right, sentence grammar off: still green — the
                        fix lives in its own section so it reads as a bonus
                        lesson, not a fail. */}
                    {grammarNote && (
                      <div className="mt-6 border-t-[2px] border-ink/20 pt-3">
                        {/* Red label — the one loud thing on a green card, so
                            the bonus lesson can't be missed. */}
                        <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-flag-red">
                          Grammar
                        </p>
                        {result.corrected && (
                          <p className="mt-1.5 font-body text-[17px] font-bold leading-snug text-ink">
                            <span className="mr-1.5 text-ink-muted">→</span>
                            {result.corrected}
                          </p>
                        )}
                        {result.error && (
                          <p className="mt-1.5 font-body text-[13px] leading-snug text-ink-soft">
                            {result.error}
                          </p>
                        )}
                      </div>
                    )}
                    {/* SATZ-007: the terse note not enough? One tap fetches a
                        plain-English unpacking of this exact correction. */}
                    {result.corrected &&
                      (!result.wordOk || !result.grammarOk) &&
                      onExplain &&
                      (explanation ? (
                        <p className="mt-3 font-body text-[13px] leading-snug text-ink-soft">
                          {explanation}
                        </p>
                      ) : (
                        <button
                          type="button"
                          onClick={handleExplain}
                          disabled={explaining}
                          className="mt-3 font-body text-[11px] font-black uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red disabled:opacity-50"
                        >
                          {explaining ? "Explaining…" : "Explain more →"}
                        </button>
                      ))}
                  </>
                ) : (
                  card.example && (
                    <p className="mt-1.5 font-body text-[15px] leading-snug text-ink">
                      {card.example}
                    </p>
                  )
                )}
              </div>
            </div>
            {/* SATZ-003: a red or peeked card offers an immediate retry next
                to the recycle — say it correctly now, while the correction
                is fresh. A green card keeps the single Next button. */}
            <div className="mt-3 flex gap-2">
              {!browsing && verdict !== "correct" && (
                <button
                  type="button"
                  onClick={tryAgain}
                  className="btn-3d inline-flex flex-1 items-center justify-center gap-2 rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-5 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-white"
                  style={redShadow}
                >
                  {"↻"} Try again
                </button>
              )}
              <button
                type="button"
                onClick={handleNext}
                className="btn-3d inline-flex flex-1 items-center justify-center gap-2 rounded-[20px] border-[3px] border-ink bg-white px-5 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
                style={inkShadow}
              >
                Next word →
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── Separate block, outside the card: the mic. Speak one sentence,
          release, verdict flips the card. Mic-only by design — no typing.
          Tap to start, tap again to stop — no auto-silence detection: a
          learner pausing to think must never trigger a premature verdict. ── */}
      <div className={`mt-6 transition-opacity ${flipped ? "opacity-60" : ""}`}>
        <div className="flex flex-col items-center">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={recording ? stopRecording : startRecording}
              disabled={flipped || checking}
              className={`btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] px-7 py-3.5 font-display text-[14px] font-black uppercase tracking-[0.16em] disabled:pointer-events-none disabled:opacity-40 ${
                recording
                  ? "animate-pulse border-flag-red-deep bg-white text-flag-red"
                  : "border-flag-red-deep bg-flag-red text-white"
              }`}
              style={redShadow}
            >
              {recording ? `Stop · ${elapsed}s` : "Record"}
            </button>
            {/* UI-006: visible only mid-recording — the escape hatch for a
                take you don't want judged. */}
            {recording && (
              <button
                type="button"
                onClick={discardRecording}
                aria-label="Discard recording"
                title="Discard recording"
                className="btn-3d inline-flex h-[52px] w-[52px] items-center justify-center rounded-[20px] border-[3px] border-ink bg-white font-display text-[18px] font-black text-ink"
                style={inkShadow}
              >
                ✕
              </button>
            )}
          </div>
          {/* Non-breaking space when idle keeps the height stable, so the
              caption appearing doesn't shove the layout around. */}
          <p className="mt-2 font-body text-[12px] font-semibold uppercase tracking-[0.18em] text-ink-muted">
            {checking
              ? "Checking your sentence…"
              : flipped
                ? scheduleLine
                : "\u00A0"}
          </p>
        </div>

        {attemptError && (
          <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-red-deep">
            {attemptError}
          </p>
        )}

        {card.example && (
          <div className="mt-7 text-center">
            {/* Gold — same tone the card back wears when you peek: revealing
                is the "hint" path, visually priced as such. In practice mode
                the peek is also a recorded lapse; browsing is just looking. */}
            <button
              type="button"
              onClick={() => {
                // Mid-recording peek: stop and discard the pending clip so
                // it never submits a verdict over the peek. The peek itself
                // is the lapse (onReveal below).
                if (recorderRef.current?.state === "recording") {
                  discardRef.current = true;
                  recorderRef.current.stop();
                }
                setRevealed(true);
                setFlipped(true);
                if (!browsing) onReveal(card.id);
              }}
              disabled={flipped || checking}
              className="btn-3d inline-flex items-center rounded-[14px] border-[3px] border-flag-gold-deep bg-flag-gold px-4 py-2 font-display text-[11px] font-black uppercase tracking-[0.18em] text-ink disabled:pointer-events-none disabled:opacity-40"
              style={goldShadow}
            >
              Reveal example
            </button>
          </div>
        )}
      </div>

      {/* Quietly parked at the bottom: prune the pool mid-practice. Deletes
          only the user_cards link — the pack reverts to "Add the rest". */}
      <div className="mt-6 text-center">
        <button
          type="button"
          onClick={handleRemove}
          disabled={removing || busy}
          className="font-body text-[11px] font-bold uppercase tracking-[0.2em] text-flag-red transition-colors hover:text-flag-red-deep disabled:opacity-40"
        >
          {removing ? "Removing…" : "✕ Remove this word from my pool"}
        </button>
      </div>
    </div>
  );
}
