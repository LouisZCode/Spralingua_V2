"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
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

// How many never-practiced words one session introduces when the caller
// doesn't hand VocabTrainer a server-decided `newAllowance` — Verbformen's
// legacy flat drip. Satzschmiede itself now gets a per-day allowance from
// the backend instead (see buildQueue below).
const NEW_PER_SESSION = 15;

// SATZ-012: the done-panel follow-up batch is deliberately small — one more
// push, not a second full session (the initial queue keeps `reviewCap`).
const MORE_CAP = 10;

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

// Today's work: due cards (most-overdue first, capped at `reviewCap` so a
// big backlog doesn't bury one session) plus a drip of new words. The drip
// size is now server-decided for Satzschmiede — `newAllowance` is 5/day
// minus whatever's already been started today, and drops to 0 while the
// accuracy guard is tripped; a caller that doesn't pass it (Verbformen)
// falls back to the old flat NEW_PER_SESSION, and a caller that doesn't pass
// `reviewCap` (Verbformen again) stays uncapped, the old behaviour.
// Selection is by overdueness/flatness only — presentation still gets a
// final shuffle so packs don't replay in authoring order. `exclude` holds
// cards already finished this session — the deck's srs is a fetch-time
// snapshot, so the server can't tell us that.
function buildQueue(
  deck: DeckCard[],
  exclude: Set<string>,
  newAllowance?: number,
  reviewCap?: number
): string[] {
  const pool = deck.filter((c) => !exclude.has(c.id));
  const due = pool
    .filter((c) => c.srs.status === "due")
    // Oldest dueAt first — most-overdue cards win the capped slots. Due
    // cards always carry a dueAt; the `?? ""` is just a defensive fallback.
    .sort((a, b) => (a.srs.dueAt ?? "").localeCompare(b.srs.dueAt ?? ""))
    .slice(0, reviewCap ?? Infinity)
    .map((c) => c.id);
  const fresh = shuffle(
    pool.filter((c) => c.srs.status === "new").map((c) => c.id)
  ).slice(0, newAllowance ?? NEW_PER_SESSION);
  return shuffle([...due, ...fresh]);
}

export default function VocabTrainer({
  deck,
  onRemove,
  onAttempt,
  onReveal,
  onExplain,
  onFlag,
  sessionPrefix = "satz",
  flow,
  onFlowDone,
  sessionId,
  newAllowance,
  reviewCap,
  newThrottled,
  onDoneChange,
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
  // SATZ-008: file a disagree-flag for this verdict (POST /satz/flag via the
  // parent). Optional — the affordance only renders when wired.
  onFlag?: (
    traceId: string,
    cardId: string | null,
    transcript: string,
    verdict: string,
    sessionId?: string
  ) => Promise<void>;
  // Langfuse practice-session id prefix (OBS-007). The Verbformen mode
  // (GRAM-002 Exercise C) reuses this trainer on a verb-only sub-deck and
  // passes "vf" so its sittings group apart from regular Satzschmiede ones.
  sessionPrefix?: string;
  // FLOW-001: mixed-practice mode — the parent deals exactly one card via
  // `deck` and remounts per turn (via `key`), so this trainer hands the
  // final verdict back instead of ever reaching its own end-of-queue state.
  flow?: boolean;
  onFlowDone?: (correct: boolean) => void;
  // OBS-007 override: the Flow page's one practice-session id for the whole
  // sitting — used in place of the ref-minted one below when present.
  sessionId?: string;
  // Server-decided daily new-word drip for Satzschmiede (0..5 — the daily
  // cap AND the accuracy-guard throttle are both already applied). Undefined
  // (Verbformen's implicit default) falls back to the flat NEW_PER_SESSION.
  newAllowance?: number;
  // Caps how many due cards one session's queue pulls in, most-overdue
  // first. Undefined (Verbformen's implicit default) stays uncapped.
  reviewCap?: number;
  // True while the accuracy guard has zeroed (or shrunk) today's allowance —
  // drives one calm reassurance line instead of new words just not showing up.
  newThrottled?: boolean;
  // SATZ-012: fires true while the post-round clean end screen is up (queue
  // exhausted AFTER real work), so the shell can hide its pool/add-cards
  // chrome. The nothing-due-at-mount panel does NOT count as done.
  onDoneChange?: (done: boolean) => void;
}) {
  // Two ways to face the pool: "practice" works today's queue (due + a drip
  // of new, shuffled — green pops a card, anything else recycles it); browse
  // is the old free walk over everything, schedule untouched by peeks.
  const [browsing, setBrowsing] = useState(false);
  // FLOW-001: the parent deals exactly one card via `deck` regardless of its
  // SRS status — buildQueue's due/new filter would drop a "later" card, so
  // flow mode takes the deck's id(s) directly instead.
  const [queue, setQueue] = useState<string[]>(() =>
    flow
      ? deck.map((c) => c.id)
      : buildQueue(deck, new Set(), newAllowance, reviewCap)
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
  const [flagState, setFlagState] = useState<"idle" | "sending" | "sent">(
    "idle"
  );

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

  // SATZ-012: `card` flipping to null is the render signal; doneRef mutates
  // in the same updates that advance the queue, so this re-runs on time.
  const roundDone = !card && doneRef.current.size > 0;
  useEffect(() => {
    onDoneChange?.(roundDone);
  }, [roundDone, onDoneChange]);

  async function startRecording() {
    if (busy || flipped || !card) return;
    setAttemptError(null);
    setResult(null);
    setExplanation(null);
    setFlagState("idle");
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
    // FLOW-001: the Flow page's own OBS-007 session id overrides this mint.
    const activeSessionId =
      sessionId ??
      (practiceSessionRef.current ??=
        sessionPrefix + "-" + crypto.randomUUID().replace(/-/g, ""));
    setChecking(true);
    try {
      const res = await onAttempt(card.id, audio, activeSessionId);
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
          sessionId ?? practiceSessionRef.current ?? undefined
        )
      );
    } catch {
      setExplanation("Couldn't fetch the explanation — try again in a moment.");
    } finally {
      setExplaining(false);
    }
  }

  // SATZ-008: one tap files the disagreement on the judgement's own trace —
  // human ground truth for tuning the examiner.
  async function handleFlag() {
    if (!result?.traceId || !onFlag || flagState !== "idle") return;
    setFlagState("sending");
    try {
      await onFlag(
        result.traceId,
        card?.id ?? null,
        result.transcript,
        `wordOk=${result.wordOk} grammarOk=${result.grammarOk}` +
          (result.corrected ? ` corrected=${result.corrected}` : ""),
        sessionId ?? practiceSessionRef.current ?? undefined
      );
      setFlagState("sent");
    } catch {
      setFlagState("idle");
    }
  }

  // Reset the per-card scratch state on every move.
  function resetScratch() {
    setRevealed(false);
    setFlipped(false);
    setResult(null);
    setExplanation(null);
    setFlagState("idle");
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
    // FLOW-001: the dealt deck is exactly one card — there's nothing to
    // recycle a miss into or pop a green out of. Hand the verdict straight
    // to the parent, which deals the next item (a fresh mount, via `key`).
    if (flow) {
      onFlowDone?.(passed);
      return;
    }
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

  // Done-panel "+ Noch N üben": pull the next small MORE_CAP batch of due
  // cards, most-overdue-first. Reuses buildQueue with newAllowance forced to
  // 0 so it can never smuggle in new words beyond today's allowance — that
  // button only ever offers due cards.
  function reviewMore() {
    setQueue(buildQueue(deck, doneRef.current, 0, MORE_CAP));
    resetScratch();
  }

  // Done-panel "+ Practice ahead": once due and new are both spent, offer up
  // to 10 not-yet-due "later" cards, soonest-dueAt-first. They're graded
  // normally like anything else, so an early success climbs the SRS ladder
  // a rung ahead of schedule — a mild net positive, not a bug.
  function practiceAhead() {
    const ahead = deck
      .filter((c) => c.srs.status === "later" && !doneRef.current.has(c.id))
      .sort((a, b) => (a.srs.dueAt ?? "").localeCompare(b.srs.dueAt ?? ""))
      .slice(0, 10)
      .map((c) => c.id);
    setQueue(shuffle(ahead));
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
    // mounts the trainer on a non-empty pool). Priority for what's next:
    // more due beyond this session's review cap first, then a chance to get
    // a head start on upcoming "later" cards. New words are deliberately
    // never offered here beyond the server's allowance — that's the whole
    // point of the re-dosed drip (see buildQueue above).
    const dueRemaining = deck.filter(
      (c) => c.srs.status === "due" && !doneRef.current.has(c.id)
    ).length;
    const laterAhead = deck.filter(
      (c) => c.srs.status === "later" && !doneRef.current.has(c.id)
    ).length;
    const reviewMoreCount = Math.min(dueRemaining, MORE_CAP);
    const finished = doneRef.current.size > 0;
    return (
      <div className="mx-auto w-full max-w-xl text-center">
        <h2 className="font-display text-[clamp(26px,5vw,36px)] font-black leading-tight tracking-tight text-ink">
          {finished ? "Alles geschmiedet!" : "Heute ist nichts fällig."}
        </h2>
        <p className="mx-auto mt-3 max-w-[380px] font-body text-[15px] leading-relaxed text-ink-soft">
          {finished
            ? dueRemaining > 0
              ? dueRemaining === 1
                ? "Runde geschafft — ein fälliges Wort wartet noch."
                : `Runde geschafft — ${dueRemaining} fällige Wörter warten noch.`
              : laterAhead > 0
                ? "Runde geschafft — du kannst ein paar kommende Wörter vorziehen."
                : "Runde geschafft — deine Wörter ruhen sich jetzt aus."
            : laterAhead > 0
              ? "Du kannst ein paar kommende Wörter vorziehen."
              : "Deine Wörter ruhen sich aus — sie kommen zurück, wenn es Zeit ist."}
        </p>
        {/* SATZ-012: the clean end keeps exactly two actions — one small
            follow-up batch and the way back. The throttle note and the
            browse link stay off the post-round screen; the nothing-due
            panel keeps them, it's a landing state, not an ending. */}
        {!finished && newThrottled && (
          <p className="mx-auto mt-2 max-w-[380px] font-body text-[12px] font-semibold text-ink-faint">
            Neue Wörter pausieren, bis sich deine Genauigkeit erholt hat —
            erledigte Reviews bringen sie zurück.
          </p>
        )}
        {dueRemaining > 0 ? (
          <button
            type="button"
            onClick={reviewMore}
            className="btn-3d mt-7 inline-flex items-center gap-2 rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3.5 font-display text-[15px] font-black uppercase tracking-[0.16em] text-white"
            style={redShadow}
          >
            + Noch {reviewMoreCount} üben
          </button>
        ) : (
          laterAhead > 0 && (
            <button
              type="button"
              onClick={practiceAhead}
              className="btn-3d mt-7 inline-flex items-center gap-2 rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3.5 font-display text-[15px] font-black uppercase tracking-[0.16em] text-white"
              style={redShadow}
            >
              + Ein paar vorziehen
            </button>
          )
        )}
        <div className="mt-4">
          <Link
            href="/practice"
            className="btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] border-ink bg-white px-7 py-3.5 font-display text-[14px] font-black uppercase tracking-[0.16em] text-ink"
            style={inkShadow}
          >
            Zurück zum Menü
          </Link>
        </div>
        {!finished && (
          <div className="mt-6">
            <button
              type="button"
              onClick={() => setBrowsing(true)}
              className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
            >
              Alle {total} Wörter ansehen →
            </button>
          </div>
        )}
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

      {/* Re-dosed drip: one calm line explaining why new words are thin
          today, instead of the drip just silently not showing up. */}
      {!browsing && newThrottled && (
        <p className="mb-3 text-center font-body text-[11px] font-semibold text-ink-faint">
          New words are paused while your accuracy recovers — clearing
          reviews brings them back.
        </p>
      )}

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
                    {/* SATZ-008: recourse for a verdict that feels wrong —
                        shown on green cards too (a miss the examiner passed
                        is exactly what we want flagged). */}
                    {result.traceId && onFlag && (
                      <p className="mt-3">
                        {flagState === "sent" ? (
                          <span className="font-body text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
                            Flagged — thanks, this tunes the examiner
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={handleFlag}
                            disabled={flagState === "sending"}
                            className="font-body text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint transition-colors hover:text-flag-red disabled:opacity-50"
                          >
                            {flagState === "sending"
                              ? "Flagging…"
                              : "Verdict seems wrong? Flag it"}
                          </button>
                        )}
                      </p>
                    )}
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
