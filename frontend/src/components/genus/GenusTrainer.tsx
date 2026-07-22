"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type {
  Article,
  ArticleVerdict,
  EndingSheet,
  GenusItem,
  GenusPool,
  PhraseVerdict,
} from "./api";

// A missed item returns once at the end of the round — same second-chance
// contract as ZeitfaerbungTrainer. `retry` marks the copy. An item counts as
// missed when EITHER beat (drag or phrase) slipped on the first try.
type QueueItem = GenusItem & { retry?: boolean };

type Phase = "intro" | "drill" | "done";

const ARTICLES: Article[] = ["der", "die", "das"];

// Gender colors — the exact scheme the Wortschatz-Pool already installed
// (VocabTrainer's ARTICLE_COLOR): der=red, die=blue, das=green. The palette
// has no blue token (flag colors), so `die` keeps the functional one-offs.
// The three characters carry the mnemonic and ARE their own gender:
// der Held · die Fee · das Baby.
const ARTICLE_UI: Record<
  Article,
  {
    bg: string;
    text: string;
    border: string;
    soft: string;
    shadowColor: string;
    emoji: string;
    character: string;
  }
> = {
  der: {
    bg: "bg-flag-red",
    text: "text-flag-red",
    border: "border-flag-red",
    soft: "bg-flag-red-soft",
    shadowColor: "var(--color-flag-red-deep)",
    emoji: "🦸",
    character: "der Held",
  },
  die: {
    bg: "bg-[#2563eb]",
    text: "text-[#2563eb]",
    border: "border-[#2563eb]",
    soft: "bg-[#dbeafe]",
    shadowColor: "#1e40af",
    emoji: "🧚",
    character: "die Fee",
  },
  das: {
    bg: "bg-success",
    text: "text-success",
    border: "border-success",
    soft: "bg-success-soft",
    shadowColor: "#166534",
    emoji: "👶",
    character: "das Baby",
  },
};

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

export default function GenusTrainer({
  round,
  endings,
  pools,
  selectedPool,
  onSelectPool,
  onArticle,
  onPhrase,
  onNewRound,
  flow,
  onFlowDone,
}: {
  round: GenusItem[];
  // The intro cheat sheet (GET /genus/rules) — nullable so a slow/failed
  // fetch degrades to an intro without the columns, never a broken one.
  endings?: EndingSheet | null;
  // The selectable curated pools (intro chips). Selecting one is handled by
  // the parent: it refetches the round, which remounts this trainer.
  pools?: GenusPool[] | null;
  selectedPool?: string | null;
  onSelectPool?: (id: string) => void;
  // Grade one dropped article / one typed phrase (POST /genus/attempts via
  // the parent, which owns the token and the OBS-007 practice-session id).
  onArticle: (itemId: string, article: Article) => Promise<ArticleVerdict>;
  onPhrase: (itemId: string, answer: string) => Promise<PhraseVerdict>;
  // Fetch a fresh round; the parent remounts this component with it.
  onNewRound: () => void;
  // FLOW-001: in flow mode the parent deals exactly one item per mount and
  // this trainer runs the DRAG BEAT ONLY — the gender choice is the rep;
  // the typed production stays a standalone-page exercise. `onPhrase` is
  // never called in flow but stays required for prop parity.
  flow?: boolean;
  onFlowDone?: (correct: boolean) => void;
}) {
  const [phase, setPhase] = useState<Phase>(flow ? "drill" : "intro");
  // Mid-drill peek at the ending cheat sheet — beginners forget the endings
  // constantly at first; the sheet stays open across items until closed.
  const [showSheet, setShowSheet] = useState(false);
  const [queue, setQueue] = useState<QueueItem[]>(round);
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  // Beat 1 — the drag. `drop` holds the CORRECT-drop verdict (wrong drops
  // only shake); `trapNote` shows "Falle!" when a trap just caught them.
  const [drop, setDrop] = useState<ArticleVerdict | null>(null);
  const [trapNote, setTrapNote] = useState<string | null>(null);
  const [shakeKey, setShakeKey] = useState(0);
  const [dragPos, setDragPos] = useState<{
    a: Article;
    x: number;
    y: number;
  } | null>(null);
  const [overTarget, setOverTarget] = useState(false);
  const dragRef = useRef<Article | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);

  // Beat 2 — the typed phrase. `guidance` holds an "unrecognized" note:
  // shown under the input, never scored, the item stays live.
  const [value, setValue] = useState("");
  const [verdict, setVerdict] = useState<PhraseVerdict | null>(null);
  const [guidance, setGuidance] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Scoring: an item is a first-try green only when BOTH beats were clean.
  const slippedRef = useRef(false);
  const [firstTryGreens, setFirstTryGreens] = useState(0);
  const [missed, setMissed] = useState<{ item: GenusItem; expected: string }[]>(
    []
  );

  const item = queue[index];

  useEffect(() => {
    if (drop && verdict === null) {
      inputRef.current?.focus();
    }
  }, [drop, verdict]);

  const advance = useCallback(() => {
    // Flow deals the drag beat only, so a clean item there is a first-try
    // drop; standalone additionally needs the phrase beat green.
    const clean = flow
      ? drop !== null && !slippedRef.current
      : (verdict?.correct ?? false) && !slippedRef.current;
    setDrop(null);
    setTrapNote(null);
    setVerdict(null);
    setValue("");
    setGuidance(null);
    setFailed(null);
    setShakeKey(0);
    slippedRef.current = false;
    // FLOW-001: no "done" phase in flow mode — hand the result to the parent.
    if (flow) {
      onFlowDone?.(clean);
      return;
    }
    if (index + 1 >= queue.length) {
      setPhase("done");
    } else {
      setIndex(index + 1);
    }
  }, [index, queue.length, flow, verdict, drop, onFlowDone]);

  // Enter advances from the feedback state (the input is unmounted then) —
  // in flow, the item ends at the drop, so Enter advances from there.
  useEffect(() => {
    if (verdict === null && !(flow && drop)) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        advance();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [verdict, flow, drop, advance]);

  const firstSlip = useCallback(() => {
    if (slippedRef.current) return;
    slippedRef.current = true;
    if (!flow && !item.retry) {
      setQueue((q) => [...q, { ...item, retry: true }]);
    }
  }, [flow, item]);

  async function submitDrop(a: Article) {
    if (busy || drop) return;
    setBusy(true);
    setFailed(null);
    try {
      const res = await onArticle(item.id, a);
      if (res.correct) {
        setTrapNote(null);
        setDrop(res);
      } else {
        setShakeKey((k) => k + 1);
        setTrapNote(res.trapped ? (res.note ?? null) : null);
        firstSlip();
      }
    } catch (err) {
      setFailed(
        err instanceof Error && err.message && !err.message.includes("failed (")
          ? err.message
          : "Couldn't check that — try again in a moment."
      );
    } finally {
      setBusy(false);
    }
  }
  // Latest-call ref so the window-level pointerup (attached once per drag)
  // never fires a stale closure.
  const submitDropRef = useRef(submitDrop);
  submitDropRef.current = submitDrop;

  function beginDrag(a: Article, e: React.PointerEvent) {
    if (busy || drop || phase !== "drill") return;
    e.preventDefault();
    dragRef.current = a;
    setDragPos({ a, x: e.clientX, y: e.clientY });
  }

  const dragActive = dragPos !== null;
  useEffect(() => {
    if (!dragActive) return;
    const isOver = (e: PointerEvent) => {
      const r = cardRef.current?.getBoundingClientRect();
      return (
        !!r &&
        e.clientX >= r.left &&
        e.clientX <= r.right &&
        e.clientY >= r.top &&
        e.clientY <= r.bottom
      );
    };
    const move = (e: PointerEvent) => {
      setDragPos((d) => (d ? { ...d, x: e.clientX, y: e.clientY } : d));
      setOverTarget(isOver(e));
    };
    const finish = () => {
      dragRef.current = null;
      setDragPos(null);
      setOverTarget(false);
    };
    const up = (e: PointerEvent) => {
      const a = dragRef.current;
      const hit = isOver(e);
      finish();
      if (a && hit) void submitDropRef.current(a);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", finish);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", finish);
    };
  }, [dragActive]);

  async function checkPhrase(e: React.FormEvent) {
    e.preventDefault();
    const answer = value.trim();
    if (!answer || busy || verdict) return;
    setBusy(true);
    setFailed(null);
    try {
      const res = await onPhrase(item.id, answer);
      if (res.kind === "unrecognized") {
        // Not a graded attempt — show the guidance, clear the box, stay put.
        setGuidance(res.note);
        setValue("");
        return;
      }
      setGuidance(null);
      if (!res.correct) firstSlip();
      setVerdict(res);
      if (!item.retry) {
        if (res.correct && !slippedRef.current) {
          setFirstTryGreens((n) => n + 1);
        } else {
          setMissed((m) => [...m, { item, expected: res.expected ?? "" }]);
        }
      }
    } catch (err) {
      setFailed(
        err instanceof Error && err.message && !err.message.includes("failed (")
          ? err.message
          : "Couldn't check that — try again in a moment."
      );
    } finally {
      setBusy(false);
    }
  }

  if (phase === "intro") {
    // No instruction wall — the cheat sheet IS the intro: the endings each
    // character owns, one trap warning, start.
    return (
      <div
        className="rounded-[28px] border-[3px] border-ink bg-white p-7"
        style={inkShadow}
      >
        {pools && pools.length > 1 && (
          <div className="mb-6">
            <p className="text-center font-body text-[11px] font-bold uppercase tracking-[0.24em] text-ink-muted">
              Wortpool
            </p>
            <div className="mt-2.5 flex flex-wrap justify-center gap-2">
              {pools.map((p) => {
                const active = p.id === selectedPool;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => !active && onSelectPool?.(p.id)}
                    aria-pressed={active}
                    className={`rounded-full border-[3px] px-3.5 py-1.5 font-body text-[12px] font-bold transition-colors ${
                      active
                        ? "border-ink bg-ink text-white"
                        : "border-ink bg-white text-ink hover:bg-paper-warm"
                    }`}
                  >
                    {p.title}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <CheatSheet endings={endings} />
        <p className="mt-5 text-center font-body text-[13px] font-semibold text-flag-gold-deep">
          ⚠️ Vorsicht, Fallen! Some words wear an ending that lies — der Name
          looks like die. No pattern? Guess der.
        </p>
        <div className="mt-6 text-center">
          <button
            type="button"
            onClick={() => setPhase("drill")}
            className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-white"
            style={redShadow}
          >
            Start · {round.length} words
          </button>
        </div>
      </div>
    );
  }

  if (phase === "done") {
    return (
      <div
        className="rounded-[28px] border-[3px] border-ink bg-white p-7 text-center"
        style={inkShadow}
      >
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
          Round complete
        </p>
        <h2 className="mt-2 font-display text-[clamp(28px,5vw,40px)] font-black tracking-tight text-ink">
          {firstTryGreens} / {round.length} first try
        </h2>
        {missed.length > 0 && (
          <div className="mx-auto mt-5 max-w-[440px] text-left">
            <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              Worth another look
            </p>
            <ul className="mt-2 space-y-1.5">
              {missed.map((m) => (
                <li
                  key={m.item.id}
                  className="font-body text-[14px] text-ink-soft"
                >
                  {m.item.noun} →{" "}
                  <span className="font-bold text-ink">{m.expected}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-7 flex items-center justify-center gap-5">
          <button
            type="button"
            onClick={onNewRound}
            className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-white"
            style={redShadow}
          >
            New round
          </button>
          <Link
            href="/practice"
            className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
          >
            ← Back to menu
          </Link>
        </div>
      </div>
    );
  }

  const solved = verdict !== null;
  const tint = !solved
    ? "border-ink bg-white"
    : verdict.correct
      ? "border-success bg-success-soft"
      : "border-flag-red bg-flag-red-soft";

  return (
    <div>
      {/* FLOW-001: the Flow page shows its own progress — hide this round's. */}
      {!flow && (
        <div className="mb-3 flex items-center justify-between">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {index + 1} / {queue.length}
            {item.retry ? " · second try" : ""}
          </p>
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            {firstTryGreens} ✓
          </p>
        </div>
      )}

      <div
        className={`rounded-[28px] border-[3px] p-7 transition-colors ${tint}`}
        style={inkShadow}
      >
        {/* Beat 1 — the three circles, live until the correct drop lands. */}
        {!drop && (
          <div className="mb-6 flex items-center justify-center gap-6">
            {ARTICLES.map((a) => {
              const ui = ARTICLE_UI[a];
              return (
                <button
                  key={a}
                  type="button"
                  onPointerDown={(e) => beginDrag(a, e)}
                  aria-label={`drag ${a}`}
                  className={`grid h-16 w-16 cursor-grab touch-none select-none place-items-center rounded-full border-[3px] border-ink font-display text-[17px] font-black text-white ${ui.bg} ${
                    dragPos?.a === a ? "opacity-30" : ""
                  }`}
                  style={{ boxShadow: `0 5px 0 ${ui.shadowColor}` }}
                >
                  {a}
                </button>
              );
            })}
          </div>
        )}

        {/* The word card — the drop target. Dashed while it waits; the
            gender color moves INTO the word once anchored and stays. */}
        <div
          key={shakeKey}
          ref={cardRef}
          className={`mx-auto max-w-[420px] rounded-[20px] border-[3px] px-6 py-5 text-center transition-colors ${
            shakeKey > 0 && !drop ? "gn-shake" : ""
          } ${
            !drop
              ? overTarget
                ? "border-dashed border-flag-gold bg-flag-gold-soft"
                : "border-dashed border-ink bg-paper-warm"
              : "border-ink bg-white"
          }`}
        >
          <p className="font-display text-[clamp(26px,5vw,36px)] font-black tracking-tight text-ink">
            {drop?.article ? (
              <>
                <span className={ARTICLE_UI[drop.article].text}>
                  {drop.article}
                </span>{" "}
                <NounDisplay noun={item.noun} drop={drop} />
              </>
            ) : (
              item.noun
            )}
          </p>
          {item.gloss && (
            <p className="mt-1 font-body text-[13px] italic text-ink-muted">
              {item.gloss}
            </p>
          )}
          {!drop && (
            <p className="mt-3 font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              drop der · die · das here
            </p>
          )}
        </div>

        {!drop && trapNote && (
          <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-gold-deep">
            {trapNote}
          </p>
        )}
        {!drop && failed && (
          <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-red-deep">
            {failed}
          </p>
        )}

        {/* Beat 2 — anchor card + production, only after the drop landed.
            Flow ends the item at the drop: anchor + Next, no typing. */}
        {drop && (
          <div className="mt-5">
            <AnchorCard drop={drop} />

            {flow ? (
              <div className="mt-5 flex items-center justify-center gap-4">
                <button
                  type="button"
                  onClick={advance}
                  className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-white px-6 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
                  style={inkShadow}
                >
                  Next
                </button>
                <span className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                  or press Enter
                </span>
              </div>
            ) : !solved ? (
              <form onSubmit={checkPhrase} className="mt-6">
                <p className="text-center font-body text-[15px] text-ink">
                  Now build it:{" "}
                  <span className="font-black">{item.adjective}</span>
                  <span className="mx-1.5 text-ink-muted">+</span>
                  <span className="font-black">{item.noun}</span>
                </p>
                <p className="mt-1 text-center font-body text-[12px] text-ink-muted">
                  ein/der + adjective + noun — or write any sentence with it;
                  only the gender is judged
                </p>
                <div className="mt-4 flex flex-col gap-3 sm:flex-row">
                  <input
                    ref={inputRef}
                    type="text"
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    placeholder="Type the phrase…"
                    lang="de"
                    autoCapitalize="off"
                    autoComplete="off"
                    autoCorrect="off"
                    spellCheck={false}
                    maxLength={140}
                    className="min-w-0 flex-1 rounded-[18px] border-[3px] border-ink bg-white px-4 py-3 font-body text-[16px] text-ink outline-none placeholder:text-ink-faint focus:border-flag-red"
                  />
                  <button
                    type="submit"
                    disabled={busy || !value.trim()}
                    className="btn-3d inline-flex items-center justify-center rounded-[18px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-white disabled:pointer-events-none disabled:opacity-40"
                    style={redShadow}
                  >
                    {busy ? "Checking…" : "Check"}
                  </button>
                </div>
                {guidance && (
                  <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-gold-deep">
                    {guidance}
                  </p>
                )}
                {failed && (
                  <p className="mt-3 text-center font-body text-[13px] font-semibold text-flag-red-deep">
                    {failed}
                  </p>
                )}
              </form>
            ) : (
              <div className="mt-6">
                <div className="flex flex-col items-center gap-2">
                  {verdict.correct ? (
                    <p className="font-body text-[18px] font-bold leading-snug text-success">
                      {verdict.expected}
                    </p>
                  ) : (
                    <>
                      <p className="font-body text-[18px] font-bold leading-snug text-ink">
                        <span className="mr-1.5 text-flag-red-deep">→</span>
                        {verdict.expected}
                      </p>
                      <p className="font-body text-[13px] text-ink-soft">
                        You typed:{" "}
                        <TypedEcho
                          value={value.trim()}
                          wrongIndex={verdict.wrongIndex}
                        />
                      </p>
                      {verdict.note && (
                        <p className="mt-1 max-w-[420px] text-center font-body text-[13px] leading-snug text-ink-soft">
                          {verdict.note}
                        </p>
                      )}
                    </>
                  )}
                </div>
                <div className="mt-5 flex items-center justify-center gap-4">
                  <button
                    type="button"
                    onClick={advance}
                    className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-white px-6 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
                    style={inkShadow}
                  >
                    {flow ? "Next" : index + 1 >= queue.length ? "Finish" : "Next"}
                  </button>
                  <span className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
                    or press Enter
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Peekable cheat sheet — the same columns as the intro, one tap away
          for when the ending won't come to mind. */}
      {endings && (
        <div className="mt-4 text-center">
          <button
            type="button"
            onClick={() => setShowSheet((v) => !v)}
            aria-expanded={showSheet}
            className="btn-3d inline-flex items-center gap-2 rounded-full border-[3px] border-ink bg-white px-4 py-2 font-display text-[11px] font-black uppercase tracking-[0.16em] text-ink"
            style={inkShadow}
          >
            Endungen
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`h-3.5 w-3.5 transition-transform ${
                showSheet ? "rotate-180" : ""
              }`}
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
          {showSheet && (
            <div className="mt-4 text-left">
              <CheatSheet endings={endings} />
            </div>
          )}
        </div>
      )}

      {/* The ghost circle riding the pointer mid-drag. */}
      {dragPos && (
        <div
          className={`pointer-events-none fixed z-[100] grid h-16 w-16 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-[3px] border-ink font-display text-[17px] font-black text-white ${ARTICLE_UI[dragPos.a].bg}`}
          style={{ left: dragPos.x, top: dragPos.y }}
        >
          {dragPos.a}
        </div>
      )}
    </div>
  );
}

// The ending columns — one per character. Shared between the intro screen
// and the mid-drill "Endungen" peek.
function CheatSheet({ endings }: { endings?: EndingSheet | null }) {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {ARTICLES.map((a) => {
        const ui = ARTICLE_UI[a];
        return (
          <div
            key={a}
            className={`rounded-[20px] border-[3px] p-4 ${ui.border} ${ui.soft}`}
          >
            <p
              className={`text-center font-display text-[22px] font-black ${ui.text}`}
            >
              {a}
            </p>
            <p className="mt-0.5 text-center font-body text-[11px] font-bold uppercase tracking-[0.18em] text-ink-muted">
              {ui.emoji} {ui.character}
            </p>
            <div className="mt-3 flex flex-wrap justify-center gap-1.5">
              {(endings?.[a] ?? []).map((e) => (
                <span
                  key={e}
                  className="rounded-full border-2 border-ink bg-white px-2.5 py-0.5 font-body text-[12px] font-bold text-ink"
                >
                  {e}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// The learner's answer echoed back with ONLY the offending word in red —
// never strikethrough (it makes the text unreadable); red alone marks the
// error.
function TypedEcho({
  value,
  wrongIndex,
}: {
  value: string;
  wrongIndex: number | null;
}) {
  const tokens = value.split(/\s+/);
  return (
    <span className="font-semibold">
      {tokens.map((t, i) => (
        <span
          key={i}
          className={i === wrongIndex ? "text-flag-red-deep" : "text-ink"}
        >
          {t}
          {i < tokens.length - 1 ? " " : ""}
        </span>
      ))}
    </span>
  );
}

// The noun with its anchored color: rule words keep the matched ending
// (or Ge- prefix) bold + colored + underlined — "the bold stays on there";
// traps and pattern-free words tint the whole word instead (there is no
// trustworthy segment to highlight).
function NounDisplay({ noun, drop }: { noun: string; drop: ArticleVerdict }) {
  const ui = ARTICLE_UI[drop.article!];
  const seg = drop.segment;
  if (!seg) {
    return <span className={ui.text}>{noun}</span>;
  }
  const mark = `${ui.text} underline decoration-[3px] underline-offset-4`;
  if (seg.kind === "suffix") {
    return (
      <>
        {noun.slice(0, noun.length - seg.text.length)}
        <span className={mark}>{seg.text}</span>
      </>
    );
  }
  return (
    <>
      <span className={mark}>{seg.text}</span>
      {noun.slice(seg.text.length)}
    </>
  );
}

// The teaching moment after a correct drop: the character's anchor scene for
// rule words, the "Falle!" why for traps, the plain memory line otherwise.
function AnchorCard({ drop }: { drop: ArticleVerdict }) {
  const ui = ARTICLE_UI[drop.article!];
  if (drop.trap) {
    return (
      <div className="rounded-[20px] border-[3px] border-flag-gold-deep bg-flag-gold-soft p-4 text-center">
        <p className="font-body text-[11px] font-black uppercase tracking-[0.2em] text-flag-gold-deep">
          Falle!
        </p>
        <p className="mt-1.5 font-body text-[13px] leading-snug text-ink">
          {drop.note}
        </p>
      </div>
    );
  }
  if (drop.anchor) {
    return (
      <div
        className={`rounded-[20px] border-[3px] p-4 text-center ${ui.border} ${ui.soft}`}
      >
        <p
          className={`font-body text-[11px] font-black uppercase tracking-[0.2em] ${ui.text}`}
        >
          {ui.emoji} {ui.character}
          {drop.reliability ? ` · ${drop.reliability}` : ""}
        </p>
        <p className="mt-1.5 font-body text-[14px] leading-snug text-ink">
          {drop.anchor}
        </p>
      </div>
    );
  }
  if (!drop.note) return null;
  return (
    <div className="rounded-[20px] border-[3px] border-ink bg-paper-warm p-4 text-center">
      <p className="font-body text-[11px] font-black uppercase tracking-[0.2em] text-ink-muted">
        Kein Muster
      </p>
      <p className="mt-1.5 font-body text-[13px] leading-snug text-ink">
        {drop.note}
      </p>
    </div>
  );
}
