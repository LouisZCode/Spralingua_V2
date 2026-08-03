"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";

import BauteilTrainer from "./bauteil/BauteilTrainer";
import {
  fetchRound as fetchBauteilRound,
  submitAttempt as submitBauteilAttempt,
  giveUp as giveUpBauteil,
  type RoundItem as BauteilItem,
  type BauteilVerdict,
} from "./bauteil/api";

import VerbindungenTrainer from "./verbindungen/VerbindungenTrainer";
import {
  fetchRound as fetchVerbindungenRound,
  submitAttempt as submitVerbindungenAttempt,
  giveUp as giveUpVerbindungen,
  type ChunkItem,
  type ChunkVerdict,
} from "./verbindungen/api";

import ZeitfaerbungTrainer from "./zeitfaerbung/ZeitfaerbungTrainer";
import {
  fetchRound as fetchZeitfaerbungRound,
  submitAttempt as submitZeitfaerbungAttempt,
  giveUp as giveUpZeitfaerbung,
  type ZeitItem,
  type ZeitVerdict,
} from "./zeitfaerbung/api";

import GenusTrainer from "./genus/GenusTrainer";
import {
  fetchRound as fetchGenusRound,
  fetchMeta as fetchGenusMeta,
  submitArticle as submitGenusArticle,
  submitPhrase as submitGenusPhrase,
  giveUpArticle as giveUpGenusArticle,
  type Article as GenusArticle,
  type ArticleVerdict as GenusArticleVerdict,
  type GenusItem,
  type PhraseVerdict as GenusPhraseVerdict,
} from "./genus/api";

import SprechenTrainer from "./sprechen/SprechenTrainer";
import {
  fetchRound as fetchSprechenRound,
  submitAttempt as submitSprechenAttempt,
  giveUp as giveUpSprechen,
  type SpokenTask,
  type SprechenVerdict,
} from "./sprechen/api";

import VocabTrainer from "./satzschmiede/VocabTrainer";
import {
  fetchDeck as fetchSatzDeck,
  submitAttempt as submitSatzAttempt,
  removeCard as removeSatzCard,
  revealCard as revealSatzCard,
  genderMissCard as genderMissSatzCard,
  explainAttempt,
  flagVerdict,
  addWord,
  fetchGloss,
  UnauthorizedError,
  type AttemptResult,
  type GlossInfo,
} from "./satzschmiede/api";
import type { DeckCard } from "./satzschmiede/deck";

import {
  fetchDeck as fetchVerbformenDeck,
  submitAttempt as submitVerbformenAttempt,
  removeCard as removeVerbformenCard,
  revealCard as revealVerbformenCard,
} from "./verbformen/api";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// FLOW-001: the seven drills this mode draws from — Szenario, Tandem
// and Conversation Practice are deliberately not in the rotation.
// Genus deals its DRAG BEAT only (the gender choice); the typed production
// stays a standalone-page exercise.
type SourceKind =
  | "satz"
  | "verbformen"
  | "bauteil"
  | "verbindungen"
  | "zeitfaerbung"
  | "sprechen"
  | "genus";

const ALL_KINDS: SourceKind[] = [
  "satz",
  "verbformen",
  "bauteil",
  "verbindungen",
  "zeitfaerbung",
  "sprechen",
  "genus",
];

const KICKER: Record<SourceKind, string> = {
  satz: "WORTSCHATZ",
  verbformen: "VERBFORMEN",
  bauteil: "BAUTEIL",
  verbindungen: "VERBINDUNGEN",
  zeitfaerbung: "ZEITFÄRBUNG",
  sprechen: "SPRECHEN",
  genus: "ARTIKEL",
};

// One dealt turn: exactly one item from exactly one source, tagged with a
// per-deal counter so the trainer remounts fresh (via `key`) every turn.
// FLOW-003: satz deals carry `rehearsal` — true for a write-free SATZ-015
// turn, false for a real graded attempt (see SatzCycle below).
type Deal =
  | { kind: "bauteil"; key: number; item: BauteilItem }
  | { kind: "verbindungen"; key: number; item: ChunkItem }
  | { kind: "zeitfaerbung"; key: number; item: ZeitItem }
  | { kind: "sprechen"; key: number; item: SpokenTask }
  | { kind: "genus"; key: number; item: GenusItem }
  | { kind: "satz"; key: number; card: DeckCard; rehearsal: boolean }
  | { kind: "verbformen"; key: number; card: DeckCard };

// FLOW-003: the only kicker that varies by more than source — a rehearsal
// satz deal says so plainly rather than presenting a write-free turn as an
// ordinary graded one. Everything else still reads straight off KICKER.
function kickerFor(deal: Deal): string {
  if (deal.kind === "satz" && deal.rehearsal) return "WORTSCHATZ · WIEDERHOLUNG";
  return KICKER[deal.kind];
}

// FLOW-004: the transition beat between deals — names the next exercise so
// the switch registers, and the mascot reacts to how the last item went.
// Seed of the bigger "raven journeys through your day's flow" direction;
// deliberately animation-only for now (no sound until the beat proves out).
type BeatMood = "happy" | "sad" | "neutral";
const BEAT_MS = 1200;

const BEAT_CAPTION: Record<BeatMood, string> = {
  happy: "Weiter so!",
  sad: "Gleich klappt's.",
  neutral: "Los geht's!",
};

function TransitionBeat({
  mood,
  title,
  first,
}: {
  mood: BeatMood;
  title: string;
  first: boolean;
}) {
  return (
    <div className="rise-in flex min-h-[300px] flex-col items-center justify-center gap-1.5">
      <Image
        src="/mascot/raven.png"
        alt=""
        width={88}
        height={88}
        className={`h-20 w-20 select-none ${
          mood === "happy" ? "mascot-hop" : mood === "sad" ? "mascot-droop" : ""
        }`}
      />
      <p className="font-body text-[13px] font-semibold text-ink-soft">
        {BEAT_CAPTION[mood]}
      </p>
      <p className="mt-3 font-body text-[11px] font-black uppercase tracking-[0.24em] text-ink-muted">
        {first ? "Erste Übung" : "Nächste Übung"}
      </p>
      <p className="font-display text-[26px] font-black tracking-tight text-ink">
        {title}
      </p>
    </div>
  );
}

function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// FLOW-003: Verbformen keeps the OLD endless cycle unchanged below — due,
// then new, then later, shuffled within each tier, rebuilt from the whole
// deck once exhausted, every deal fully graded. Its route (verbformen/api.ts)
// has no rehearsal flag (SATZ-015 is a satz-only backend feature), so there
// is nothing write-free to deal it from; changing its dosing is out of scope
// for this ticket.
type CardCycle = { deck: DeckCard[]; order: string[] };

function orderCycle(deck: DeckCard[]): string[] {
  const due = deck.filter((c) => c.srs.status === "due").map((c) => c.id);
  const fresh = deck.filter((c) => c.srs.status === "new").map((c) => c.id);
  const later = deck.filter((c) => c.srs.status === "later").map((c) => c.id);
  return [...shuffle(due), ...shuffle(fresh), ...shuffle(later)];
}

function nextCard(cycle: CardCycle): DeckCard | null {
  if (cycle.deck.length === 0) return null;
  if (cycle.order.length === 0) {
    cycle.order = orderCycle(cycle.deck);
  }
  while (cycle.order.length > 0) {
    const id = cycle.order.shift() as string;
    const card = cycle.deck.find((c) => c.id === id);
    if (card) return card;
  }
  return null;
}

// FLOW-003: satz's schedule-honest replacement for CardCycle. `gradedOrder`
// is due-first then an allowance-capped drip of new — every deal from it is
// a REAL graded POST /satz/attempts write, same dosing policy as the
// standalone trainer's buildQueue. Once it's spent, deals come from
// `rehearsalOrder` instead: write-free SATZ-015 turns (full STT+judge+
// feedback, zero schedule/ledger/attempt-log writes) drawn from the WHOLE
// deck — including cards already graded this sitting and not-yet-due
// "later" cards, since nothing there can be hurt by an ungraded rep — so
// Wortschatz stays in the rotation forever instead of re-grinding a rested
// card's interval once the honest graded slice runs out. Cards benched
// (leeched, SATZ P2) are excluded from both orders and from every rebuild.
type SatzCycle = {
  deck: DeckCard[];
  gradedOrder: string[];
  rehearsalOrder: string[];
};

function nonBenchedIds(deck: DeckCard[]): string[] {
  return deck.filter((c) => c.srs.status !== "benched").map((c) => c.id);
}

// Due before new (not shuffled together like buildQueue) so an overdue
// review can never lose its priority slot to a lucky shuffle — Flow deals
// one card at a time over a long sitting, not one fixed round, so keeping
// due strictly first matters more here than in the standalone trainer.
function buildGradedOrder(deck: DeckCard[], newAllowance: number): string[] {
  const due = deck
    .filter((c) => c.srs.status === "due")
    .map((c) => c.id);
  const fresh = deck
    .filter((c) => c.srs.status === "new")
    .map((c) => c.id);
  return [...shuffle(due), ...shuffle(fresh).slice(0, newAllowance)];
}

function buildRehearsalOrder(deck: DeckCard[]): string[] {
  return shuffle(nonBenchedIds(deck));
}

// Returns the next satz card plus whether this deal is a write-free
// rehearsal turn. Drains gradedOrder first; once empty, deals from (and
// endlessly rebuilds) rehearsalOrder. A benched card can still be sitting in
// a stale order entry (e.g. it got leeched mid-sitting) — skip it rather
// than dealing it, per the "benched must never be dealt" invariant.
function nextSatzCard(cycle: SatzCycle): [DeckCard, boolean] | null {
  const byId = new Map(cycle.deck.map((c) => [c.id, c] as const));
  while (cycle.gradedOrder.length > 0) {
    const id = cycle.gradedOrder.shift() as string;
    const card = byId.get(id);
    if (card && card.srs.status !== "benched") return [card, false];
  }
  if (cycle.rehearsalOrder.length === 0) {
    cycle.rehearsalOrder = buildRehearsalOrder(cycle.deck);
  }
  while (cycle.rehearsalOrder.length > 0) {
    const id = cycle.rehearsalOrder.shift() as string;
    const card = byId.get(id);
    if (card && card.srs.status !== "benched") return [card, true];
  }
  return null;
}

// Every mutable buffer/cycle lives in one bag, held in a ref so the deal
// logic never has to fight React's render cycle — turns are dealt by
// mutating this bag and pushing the result into `deal` state.
type FlowBag = {
  bauteil: BauteilItem[];
  verbindungen: ChunkItem[];
  zeitfaerbung: ZeitItem[];
  sprechen: SpokenTask[];
  genus: GenusItem[];
  satz: SatzCycle;
  verbformen: CardCycle;
  dealCounter: number;
};

function emptyBag(): FlowBag {
  return {
    bauteil: [],
    verbindungen: [],
    zeitfaerbung: [],
    sprechen: [],
    genus: [],
    satz: { deck: [], gradedOrder: [], rehearsalOrder: [] },
    verbformen: { deck: [], order: [] },
    dealCounter: 0,
  };
}

function sourceCount(bag: FlowBag, kind: SourceKind): number {
  if (kind === "bauteil") return bag.bauteil.length;
  if (kind === "verbindungen") return bag.verbindungen.length;
  if (kind === "zeitfaerbung") return bag.zeitfaerbung.length;
  if (kind === "sprechen") return bag.sprechen.length;
  if (kind === "genus") return bag.genus.length;
  // FLOW-003: benched cards can never be dealt, so a deck that's 100%
  // benched must drop satz out of the rotation cleanly instead of stalling
  // pickSource on a source that always deals null. Verbformen keeps its old
  // unfiltered count (see the CardCycle comment above) — out of scope here.
  if (kind === "satz") return nonBenchedIds(bag.satz.deck).length;
  return bag.verbformen.deck.length;
}

// A random exercise different from the previous one — unless only one
// source is left standing, in which case there's nothing to vary.
function pickSource(bag: FlowBag, prevKind: SourceKind | null): SourceKind | null {
  const avail = ALL_KINDS.filter((k) => sourceCount(bag, k) > 0);
  if (avail.length === 0) return null;
  const pool =
    avail.length > 1 && prevKind ? avail.filter((k) => k !== prevKind) : avail;
  const options = pool.length > 0 ? pool : avail;
  return options[Math.floor(Math.random() * options.length)];
}

function dealFromSource(bag: FlowBag, kind: SourceKind): Deal | null {
  const key = ++bag.dealCounter;
  if (kind === "bauteil") {
    const item = bag.bauteil.shift();
    return item ? { kind, key, item } : null;
  }
  if (kind === "verbindungen") {
    const item = bag.verbindungen.shift();
    return item ? { kind, key, item } : null;
  }
  if (kind === "zeitfaerbung") {
    const item = bag.zeitfaerbung.shift();
    return item ? { kind, key, item } : null;
  }
  if (kind === "sprechen") {
    const item = bag.sprechen.shift();
    return item ? { kind, key, item } : null;
  }
  if (kind === "genus") {
    const item = bag.genus.shift();
    return item ? { kind, key, item } : null;
  }
  if (kind === "satz") {
    const dealt = nextSatzCard(bag.satz);
    return dealt ? { kind, key, card: dealt[0], rehearsal: dealt[1] } : null;
  }
  const card = nextCard(bag.verbformen);
  return card ? { kind: "verbformen", key, card } : null;
}

// Once a round buffer drops to <=1, top it up in the background — fire and
// forget, dedupe not needed (an overlapping refill just appends twice).
function refillIfLow(
  bag: FlowBag,
  kind: "bauteil" | "verbindungen" | "zeitfaerbung" | "sprechen" | "genus",
  token: string
) {
  if (kind === "bauteil" && bag.bauteil.length <= 1) {
    fetchBauteilRound(token)
      .then((items) => {
        bag.bauteil = [...bag.bauteil, ...items];
      })
      .catch(() => {});
  } else if (kind === "verbindungen" && bag.verbindungen.length <= 1) {
    fetchVerbindungenRound(token)
      .then((items) => {
        bag.verbindungen = [...bag.verbindungen, ...items];
      })
      .catch(() => {});
  } else if (kind === "zeitfaerbung" && bag.zeitfaerbung.length <= 1) {
    fetchZeitfaerbungRound(token)
      .then((items) => {
        bag.zeitfaerbung = [...bag.zeitfaerbung, ...items];
      })
      .catch(() => {});
  } else if (kind === "sprechen" && bag.sprechen.length <= 1) {
    fetchSprechenRound(token)
      .then(({ tasks }) => {
        bag.sprechen = [...bag.sprechen, ...tasks];
      })
      .catch(() => {});
  } else if (kind === "genus" && bag.genus.length <= 1) {
    fetchGenusRound(token)
      .then((items) => {
        bag.genus = [...bag.genus, ...items];
      })
      .catch(() => {});
  }
}

type Tally = { done: number; correct: number };

function emptyTallies(): Record<SourceKind, Tally> {
  return {
    satz: { done: 0, correct: 0 },
    verbformen: { done: 0, correct: 0 },
    bauteil: { done: 0, correct: 0 },
    verbindungen: { done: 0, correct: 0 },
    zeitfaerbung: { done: 0, correct: 0 },
    sprechen: { done: 0, correct: 0 },
    genus: { done: 0, correct: 0 },
  };
}

// FLOW-005: the pre-start round-length picker — 10 / 20 / ∞ presets or a
// custom 1–50, persisted across visits. Presets and the custom field are
// mutually exclusive: a non-empty custom value always wins over whichever
// preset was last picked (see the `roundChoice` derivation in Flow()), and
// picking a preset clears the custom field. The two states are never
// reconciled imperatively — the derivation IS the mutual-exclusion rule.
type RoundPreset = "10" | "20" | "inf";
type StoredRoundChoice = RoundPreset | number;

const ROUND_STORAGE_KEY = "flow-rounds-v1";

const ROUND_PRESETS: { key: RoundPreset; label: string }[] = [
  { key: "10", label: "10" },
  { key: "20", label: "20" },
  { key: "inf", label: "∞" },
];

function clampRounds(n: number): number {
  return Math.min(50, Math.max(1, Math.trunc(n)));
}

function loadStoredRoundChoice(): StoredRoundChoice {
  try {
    const raw = localStorage.getItem(ROUND_STORAGE_KEY);
    if (raw === "10" || raw === "20" || raw === "inf") return raw;
    if (raw) {
      const n = Number(raw);
      if (Number.isFinite(n)) return clampRounds(n);
    }
  } catch {}
  return "inf";
}

function persistRoundChoice(choice: StoredRoundChoice) {
  try {
    localStorage.setItem(ROUND_STORAGE_KEY, String(choice));
  } catch {}
}

function targetFromChoice(choice: StoredRoundChoice): number | null {
  if (choice === "inf") return null;
  if (choice === "10") return 10;
  if (choice === "20") return 20;
  return choice;
}

// FLOW-001: endless mixed-practice mode — one item at a time, drawn randomly
// across the seven existing exercises, running until the learner hits Finish
// or (FLOW-005) a chosen round length is reached. This component is the
// auth-guarded page shell + the dealing/tally state; each existing trainer
// runs unmodified except for its opt-in `flow` prop.
export default function Flow() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [phase, setPhase] = useState<"loading" | "error" | "ready">("loading");
  const [deal, setDeal] = useState<Deal | null>(null);
  // FLOW-004: while the beat shows, `deal` is null and the freshly-computed
  // next deal parks in pendingDealRef — a trainer must never mount (and e.g.
  // arm its mic) underneath the interstitial.
  const [beat, setBeat] = useState<{
    mood: BeatMood;
    kind: SourceKind;
    first: boolean;
  } | null>(null);
  const pendingDealRef = useRef<Deal | null>(null);
  const beatTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [finished, setFinished] = useState(false);
  const [totals, setTotals] = useState({ total: 0, correct: 0 });
  const [perExercise, setPerExercise] =
    useState<Record<SourceKind, Tally>>(emptyTallies());

  const bagRef = useRef<FlowBag>(emptyBag());

  // OBS-007: one Langfuse practice-session id for the whole sitting, minted
  // lazily on first use — every attempt across every exercise threads it, so
  // the sitting groups as one session.
  const sessionIdRef = useRef<string | null>(null);
  const sid = useCallback((): string => {
    sessionIdRef.current ??= "flow-" + crypto.randomUUID().replace(/-/g, "");
    return sessionIdRef.current;
  }, []);

  // FLOW-003: whether the CURRENTLY dealt satz card is a write-free
  // rehearsal turn — set by dealNext whenever it deals a satz card, read by
  // handleSatzAttempt so the first attempt on this card carries the right
  // rehearsal flag. Independent of VocabTrainer's own SATZ-015 rehearsalRef
  // (which arms a SAME-card retry after a graded pass with a grammar note,
  // and bypasses onAttempt entirely when armed) — the two never fire on the
  // same submission, so there's nothing to conflict.
  const satzRehearsalRef = useRef(false);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  // FLOW-005: pre-start round-length picker. `screen` gates the very first
  // deal — round buffers still prefetch in the background (the initial-load
  // effect further below is otherwise untouched), but nothing gets dealt
  // until Start is pressed.
  const [screen, setScreen] = useState<"picker" | "playing">("picker");
  const startedDealingRef = useRef(false);

  const [presetChoice, setPresetChoice] = useState<RoundPreset>("inf");
  const [customText, setCustomText] = useState<string>("");

  // SSR-safe hydration from localStorage, same idiom as TopicScreen's input
  // mode toggle — reading storage during render would mismatch the
  // server-rendered HTML.
  useEffect(() => {
    const stored = loadStoredRoundChoice();
    if (typeof stored === "number") {
      setCustomText(String(stored));
    } else if (stored !== "inf") {
      setPresetChoice(stored);
    }
  }, []);

  const customNumber = customText === "" ? null : Number(customText);
  const roundChoice: StoredRoundChoice = customNumber ?? presetChoice;
  const roundTarget = targetFromChoice(roundChoice);

  const handlePickPreset = useCallback((preset: RoundPreset) => {
    setPresetChoice(preset);
    setCustomText("");
  }, []);

  const handleCustomChange = useCallback((raw: string) => {
    const digits = raw.replace(/[^0-9]/g, "");
    if (digits === "") {
      setCustomText("");
      return;
    }
    setCustomText(String(clampRounds(Number(digits))));
  }, []);

  const handleStart = useCallback(() => {
    persistRoundChoice(roundChoice);
    setScreen("playing");
  }, [roundChoice]);

  const dealNext = useCallback(
    (prevKind: SourceKind | null, mood: BeatMood = "neutral") => {
      const bag = bagRef.current;
      const kind = pickSource(bag, prevKind);
      if (!kind) {
        setDeal(null);
        return;
      }
      const next = dealFromSource(bag, kind);
      if (!next) {
        setDeal(null);
        return;
      }
      if (
        token &&
        (kind === "bauteil" ||
          kind === "verbindungen" ||
          kind === "zeitfaerbung" ||
          kind === "sprechen" ||
          kind === "genus")
      ) {
        refillIfLow(bag, kind, token);
      }
      // FLOW-003: latch this deal's rehearsal flag before it renders — the
      // attempt handler below has no other way to know which order this
      // card was dealt from.
      satzRehearsalRef.current = next.kind === "satz" && next.rehearsal;
      // FLOW-004: park the deal behind a short transition beat that names
      // the next exercise and lets the mascot react to the last one.
      pendingDealRef.current = next;
      setDeal(null);
      setBeat({ mood, kind: next.kind, first: prevKind === null });
      if (beatTimerRef.current) clearTimeout(beatTimerRef.current);
      beatTimerRef.current = setTimeout(() => {
        setBeat(null);
        setDeal(pendingDealRef.current);
      }, BEAT_MS);
    },
    [token]
  );

  // FLOW-004: never let the beat timer fire into an unmounted component.
  useEffect(() => {
    return () => {
      if (beatTimerRef.current) clearTimeout(beatTimerRef.current);
    };
  }, []);

  // FLOW-005: fire the very first deal once both the learner has pressed
  // Start and the initial fetch has completed — whichever happens second.
  // Guarded by a ref so it only ever runs once per mount (the initial-load
  // effect below no longer deals directly).
  useEffect(() => {
    if (phase === "ready" && screen === "playing" && !startedDealingRef.current) {
      startedDealingRef.current = true;
      dealNext(null);
    }
  }, [phase, screen, dealNext]);

  // Initial load: every source fetches independently. A source that fails
  // or comes back empty just drops out of the rotation (console-silent); an
  // expired token signs out (same policy as every other practice page). Only
  // when every single source is unavailable does the page show an error.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const bag = bagRef.current;

    async function loadOne<T>(
      promise: Promise<T>,
      assign: (value: T) => void
    ): Promise<void> {
      try {
        const value = await promise;
        if (!cancelled) assign(value);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
      }
    }

    Promise.all([
      loadOne(fetchBauteilRound(token), (items) => {
        bag.bauteil = items;
      }),
      loadOne(fetchVerbindungenRound(token), (items) => {
        bag.verbindungen = items;
      }),
      loadOne(fetchZeitfaerbungRound(token), (items) => {
        bag.zeitfaerbung = items;
      }),
      loadOne(fetchSprechenRound(token), ({ tasks }) => {
        bag.sprechen = tasks;
      }),
      loadOne(fetchGenusRound(token), (items) => {
        bag.genus = items;
      }),
      loadOne(fetchSatzDeck(token), (payload) => {
        // FLOW-003: due-first + the server's dosed daily new-word drip —
        // rehearsalOrder starts empty and is only built (endlessly) once
        // gradedOrder runs dry, see nextSatzCard.
        bag.satz = {
          deck: payload.cards,
          gradedOrder: buildGradedOrder(payload.cards, payload.newAllowance),
          rehearsalOrder: [],
        };
      }),
      loadOne(fetchVerbformenDeck(token), (cards) => {
        bag.verbformen = { deck: cards, order: orderCycle(cards) };
      }),
    ]).then(() => {
      if (cancelled) return;
      const anyAvailable = ALL_KINDS.some((k) => sourceCount(bag, k) > 0);
      if (!anyAvailable) {
        setPhase("error");
        return;
      }
      // FLOW-005: don't deal yet — the picker effect above fires the first
      // deal once the learner presses Start (which may already have
      // happened, or may happen later; either order works).
      setPhase("ready");
    });

    return () => {
      cancelled = true;
    };
  }, [token, signOut, dealNext]);

  const handleItemDone = useCallback(
    (kind: SourceKind, correct: boolean) => {
      setTotals((t) => ({ total: t.total + 1, correct: t.correct + (correct ? 1 : 0) }));
      setPerExercise((p) => ({
        ...p,
        [kind]: { done: p[kind].done + 1, correct: p[kind].correct + (correct ? 1 : 0) },
      }));
      // FLOW-004: the outcome shapes the transition beat's mascot mood.
      dealNext(kind, correct ? "happy" : "sad");
    },
    [dealNext]
  );

  // FLOW-005: auto-finish once the completed tally hits a finite round's
  // target — same summary screen the manual Finish button shows. Exact
  // equality (not >=) so continuing past the target via "Keep going" never
  // bounces straight back to the summary on the very next item.
  useEffect(() => {
    if (roundTarget !== null && totals.total === roundTarget) {
      setFinished(true);
    }
  }, [totals.total, roundTarget]);

  // onNewRound is never called in flow mode — the round-trainers only reach
  // their own "done" phase without the flow flag, which we never pass.
  const noopNewRound = useCallback(() => {}, []);

  const handleBauteilAttempt = useCallback(
    async (itemId: string, answer: string): Promise<BauteilVerdict> => {
      if (!token) throw new UnauthorizedError("/bauteil/attempts");
      try {
        return await submitBauteilAttempt(token, itemId, answer, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  // FLOW-002: the deliberate "give up" escape — same auth-guarded shape as
  // the attempt handlers above, one per item drill (satz/verbformen keep
  // their existing onReveal instead; genus wires only its drag beat).
  const handleBauteilGiveUp = useCallback(
    async (itemId: string): Promise<BauteilVerdict> => {
      if (!token) throw new UnauthorizedError("/bauteil/attempts");
      try {
        return await giveUpBauteil(token, itemId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleVerbindungenAttempt = useCallback(
    async (itemId: string, answer: string): Promise<ChunkVerdict> => {
      if (!token) throw new UnauthorizedError("/verbindungen/attempts");
      try {
        return await submitVerbindungenAttempt(token, itemId, answer, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleVerbindungenGiveUp = useCallback(
    async (itemId: string): Promise<ChunkVerdict> => {
      if (!token) throw new UnauthorizedError("/verbindungen/attempts");
      try {
        return await giveUpVerbindungen(token, itemId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleZeitfaerbungAttempt = useCallback(
    async (itemId: string, answer: string): Promise<ZeitVerdict> => {
      if (!token) throw new UnauthorizedError("/zeitfaerbung/attempts");
      try {
        return await submitZeitfaerbungAttempt(token, itemId, answer, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleZeitfaerbungGiveUp = useCallback(
    async (itemId: string): Promise<ZeitVerdict> => {
      if (!token) throw new UnauthorizedError("/zeitfaerbung/attempts");
      try {
        return await giveUpZeitfaerbung(token, itemId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleSprechenAttempt = useCallback(
    async (taskId: string, audio: Blob): Promise<SprechenVerdict> => {
      if (!token) throw new UnauthorizedError("/sprechen/attempts");
      try {
        return await submitSprechenAttempt(token, taskId, audio, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleSprechenGiveUp = useCallback(
    async (taskId: string): Promise<SprechenVerdict> => {
      if (!token) throw new UnauthorizedError("/sprechen/give-up");
      try {
        return await giveUpSprechen(token, taskId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleGenusArticle = useCallback(
    async (
      itemId: string,
      article: GenusArticle
    ): Promise<GenusArticleVerdict> => {
      if (!token) throw new UnauthorizedError("/genus/attempts");
      try {
        return await submitGenusArticle(token, itemId, article, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleGenusGiveUp = useCallback(
    async (itemId: string): Promise<GenusArticleVerdict> => {
      if (!token) throw new UnauthorizedError("/genus/attempts");
      try {
        return await giveUpGenusArticle(token, itemId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  // Never called in flow (the genus item ends at the drag beat) — passed for
  // the trainer's required-prop parity with the standalone page.
  const handleGenusPhrase = useCallback(
    async (itemId: string, answer: string): Promise<GenusPhraseVerdict> => {
      if (!token) throw new UnauthorizedError("/genus/attempts");
      try {
        return await submitGenusPhrase(token, itemId, answer, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleSatzAttempt = useCallback(
    async (
      cardId: string,
      audio: Blob,
      sessionId: string
    ): Promise<AttemptResult> => {
      if (!token) throw new UnauthorizedError("/satz/attempts");
      try {
        // FLOW-003: satzRehearsalRef reflects the CURRENT deal (latched by
        // dealNext) — true routes this attempt through the write-free
        // SATZ-015 path (full STT+judge+feedback, zero schedule/ledger
        // writes), matching how it was dealt. VocabTrainer's own SATZ-015
        // retry (armed by its gold "Try again" after a grammar-corrected
        // pass) never reaches this handler at all — it bypasses onAttempt
        // and posts straight to the API with rehearsal:true itself.
        return await submitSatzAttempt(
          token,
          cardId,
          audio,
          sessionId,
          satzRehearsalRef.current
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut]
  );

  const handleVerbformenAttempt = useCallback(
    async (
      cardId: string,
      audio: Blob,
      sessionId: string
    ): Promise<AttemptResult> => {
      if (!token) throw new UnauthorizedError("/verbformen/attempts");
      try {
        return await submitVerbformenAttempt(token, cardId, audio, sessionId);
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut]
  );

  const handleSatzRemove = useCallback(
    async (cardId: string): Promise<void> => {
      if (!token) return;
      try {
        await removeSatzCard(token, cardId);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
          return;
        }
      }
      // FLOW-001: no page-level refetch here — just drop it from the local
      // cycle so future deals never pick it again. FLOW-003: both order
      // arrays need pruning now, not just one.
      const bag = bagRef.current;
      bag.satz = {
        deck: bag.satz.deck.filter((c) => c.id !== cardId),
        gradedOrder: bag.satz.gradedOrder.filter((id) => id !== cardId),
        rehearsalOrder: bag.satz.rehearsalOrder.filter((id) => id !== cardId),
      };
    },
    [token, signOut]
  );

  const handleVerbformenRemove = useCallback(
    async (cardId: string): Promise<void> => {
      if (!token) return;
      try {
        await removeVerbformenCard(token, cardId);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
          return;
        }
      }
      const bag = bagRef.current;
      bag.verbformen = {
        deck: bag.verbformen.deck.filter((c) => c.id !== cardId),
        order: bag.verbformen.order.filter((id) => id !== cardId),
      };
    },
    [token, signOut]
  );

  const handleSatzReveal = useCallback(
    (cardId: string) => {
      if (!token) return;
      // FLOW-003: a rehearsal deal is write-free END TO END — the reveal
      // lapse (interval quartered, leech counter bumped) must not sneak in
      // through this side door while the graded attempt path is flagged off.
      if (satzRehearsalRef.current) return;
      revealSatzCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) signOut();
      });
    },
    [token, signOut]
  );

  // SATZ-010: a wrong gender pick — same fire-and-forget lapse policy as a
  // reveal. Wired only to the Satzschmiede deal, not Verbformen's verb-only one.
  const handleSatzGenderMiss = useCallback(
    (cardId: string) => {
      if (!token) return;
      // FLOW-003: same write-free guarantee as the reveal above.
      if (satzRehearsalRef.current) return;
      genderMissSatzCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) signOut();
      });
    },
    [token, signOut]
  );

  // SATZ-010 hint: Artikel-Anker's ending labels, fetched on first use.
  const handleSatzGenderCues = useCallback(async () => {
    if (!token) throw new UnauthorizedError("/genus/rules");
    try {
      return (await fetchGenusMeta(token)).endings;
    } catch (e) {
      if (e instanceof UnauthorizedError) signOut();
      throw e;
    }
  }, [token, signOut]);

  const handleVerbformenReveal = useCallback(
    (cardId: string) => {
      if (!token) return;
      revealVerbformenCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) signOut();
      });
    },
    [token, signOut]
  );

  const handleExplain = useCallback(
    async (
      cardId: string,
      transcript: string,
      corrected: string,
      error: string | null,
      sessionId?: string
    ): Promise<string> => {
      if (!token) throw new UnauthorizedError("/satz/explain");
      try {
        return await explainAttempt(
          token,
          cardId,
          transcript,
          corrected,
          error,
          sessionId
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut]
  );

  const handleFlag = useCallback(
    async (
      traceId: string,
      cardId: string | null,
      transcript: string,
      verdict: string,
      sessionId?: string
    ): Promise<void> => {
      if (!token) throw new UnauthorizedError("/satz/flag");
      try {
        await flagVerdict(token, traceId, cardId, transcript, verdict, sessionId);
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut]
  );

  // UI-009: word-gloss popover wiring — Flow mounts SprechenTrainer,
  // VerbindungenTrainer and VocabTrainer without onGloss/onAdd, silently
  // dropping the glossing those trainers offer on their standalone pages.
  // Same auth-guarded pattern as every handler above, filed under the
  // sitting's own OBS-007 session id (sid()) like every other Flow attempt.
  const handleGloss = useCallback(
    async (word: string, context: string): Promise<GlossInfo> => {
      if (!token) throw new UnauthorizedError("/satz/gloss");
      try {
        return await fetchGloss(token, word, context, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  const handleAddWord = useCallback(
    async (lemma: string): Promise<{ glossRemaining?: number } | void> => {
      if (!token) throw new UnauthorizedError("/satz/cards");
      try {
        // SATZ-013: gloss-popover add — counts against the daily gloss cap.
        const res = await addWord(token, lemma, sid(), "gloss");
        return { glossRemaining: res.glossRemaining };
      } catch (e) {
        if (e instanceof UnauthorizedError) signOut();
        throw e;
      }
    },
    [token, signOut, sid]
  );

  if (!ready || !token) {
    return null;
  }

  return (
    <div className="relative flex min-h-screen flex-col bg-white text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-2.5">
            <Image
              src="/mascot/raven.png"
              alt="Spralingua raven mascot"
              width={40}
              height={40}
              priority
              className="h-9 w-9 select-none"
            />
            <span className="font-display text-[22px] font-black tracking-tight text-ink">
              Spralingua
            </span>
          </Link>
          <Link
            href="/practice"
            className="font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
          >
            ← Menu
          </Link>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-12">
        {screen === "picker" && phase !== "error" ? (
          <RoundPicker
            presetChoice={presetChoice}
            customText={customText}
            onPickPreset={handlePickPreset}
            onCustomChange={handleCustomChange}
            onStart={handleStart}
          />
        ) : (
          <>
            <div className="mb-5 text-center">
              <h1 className="font-display text-[24px] font-black tracking-tight text-ink">
                Flow
              </h1>
              <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
                alle Übungen
              </p>
            </div>

            {phase === "loading" ? null : phase === "error" ? (
              <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
                Couldn&apos;t load — is the backend running?
              </p>
            ) : finished ? (
              <SummaryCard
                totals={totals}
                perExercise={perExercise}
                onKeepGoing={() => setFinished(false)}
              />
            ) : (
              <>
                <div className="mb-4 flex items-center justify-between">
                  <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
                    {totals.total} item{totals.total === 1 ? "" : "s"} ·{" "}
                    {totals.correct} ✓
                    {roundTarget !== null && (
                      <>
                        {" "}
                        · {totals.total} / {roundTarget}
                      </>
                    )}
                  </p>
                  <button
                    type="button"
                    onClick={() => setFinished(true)}
                    className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-white px-5 py-2 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink"
                    style={inkShadow}
                  >
                    Finish
                  </button>
                </div>

                {beat !== null && (
                  <TransitionBeat
                    mood={beat.mood}
                    title={KICKER[beat.kind]}
                    first={beat.first}
                  />
                )}

                {deal !== null && (
                  <>
                    <p className="mb-3 text-center font-body text-[11px] font-black uppercase tracking-[0.24em] text-flag-red">
                      {kickerFor(deal)}
                    </p>

                    {deal.kind === "bauteil" && (
                      <BauteilTrainer
                        key={deal.key}
                        round={[deal.item]}
                        onAttempt={handleBauteilAttempt}
                        onNewRound={noopNewRound}
                        flow
                        onFlowDone={(correct) => handleItemDone("bauteil", correct)}
                        allowGiveUp
                        onGiveUp={handleBauteilGiveUp}
                      />
                    )}
                    {deal.kind === "verbindungen" && (
                      <VerbindungenTrainer
                        key={deal.key}
                        round={[deal.item]}
                        onAttempt={handleVerbindungenAttempt}
                        onNewRound={noopNewRound}
                        onGloss={handleGloss}
                        onAdd={handleAddWord}
                        flow
                        onFlowDone={(correct) =>
                          handleItemDone("verbindungen", correct)
                        }
                        allowGiveUp
                        onGiveUp={handleVerbindungenGiveUp}
                      />
                    )}
                    {deal.kind === "zeitfaerbung" && (
                      <ZeitfaerbungTrainer
                        key={deal.key}
                        round={[deal.item]}
                        onAttempt={handleZeitfaerbungAttempt}
                        onNewRound={noopNewRound}
                        flow
                        onFlowDone={(correct) =>
                          handleItemDone("zeitfaerbung", correct)
                        }
                        allowGiveUp
                        onGiveUp={handleZeitfaerbungGiveUp}
                      />
                    )}
                    {deal.kind === "sprechen" && (
                      <SprechenTrainer
                        key={deal.key}
                        round={[deal.item]}
                        onAttempt={handleSprechenAttempt}
                        onNewRound={noopNewRound}
                        onGloss={handleGloss}
                        onAdd={handleAddWord}
                        flow
                        onFlowDone={(correct) => handleItemDone("sprechen", correct)}
                        allowGiveUp
                        onGiveUp={handleSprechenGiveUp}
                      />
                    )}
                    {deal.kind === "genus" && (
                      <GenusTrainer
                        key={deal.key}
                        round={[deal.item]}
                        onArticle={handleGenusArticle}
                        onPhrase={handleGenusPhrase}
                        onNewRound={noopNewRound}
                        flow
                        onFlowDone={(correct) => handleItemDone("genus", correct)}
                        allowGiveUp
                        onGiveUp={handleGenusGiveUp}
                      />
                    )}
                    {deal.kind === "satz" && (
                      <VocabTrainer
                        key={deal.key}
                        deck={[deal.card]}
                        onRemove={handleSatzRemove}
                        onAttempt={handleSatzAttempt}
                        onReveal={handleSatzReveal}
                        onExplain={handleExplain}
                        onFlag={handleFlag}
                        sessionPrefix="satz"
                        flow
                        onFlowDone={(correct) => handleItemDone("satz", correct)}
                        sessionId={sid()}
                        onGenderMiss={handleSatzGenderMiss}
                        onGenderCues={handleSatzGenderCues}
                        onGloss={handleGloss}
                        onAdd={handleAddWord}
                      />
                    )}
                    {deal.kind === "verbformen" && (
                      <VocabTrainer
                        key={deal.key}
                        deck={[deal.card]}
                        onRemove={handleVerbformenRemove}
                        onAttempt={handleVerbformenAttempt}
                        onReveal={handleVerbformenReveal}
                        onExplain={handleExplain}
                        onFlag={handleFlag}
                        sessionPrefix="vf"
                        flow
                        onFlowDone={(correct) =>
                          handleItemDone("verbformen", correct)
                        }
                        sessionId={sid()}
                        onGloss={handleGloss}
                        onAdd={handleAddWord}
                      />
                    )}
                  </>
                )}
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}

// ─── Finish summary ──────────────────────────────────────────────────────
// Same card language as the trainers' own "done" screens: big score, a
// breakdown list, a primary action to resume, a quiet link back to the menu.

function SummaryCard({
  totals,
  perExercise,
  onKeepGoing,
}: {
  totals: { total: number; correct: number };
  perExercise: Record<SourceKind, Tally>;
  onKeepGoing: () => void;
}) {
  const rows = ALL_KINDS.filter((k) => perExercise[k].done > 0);
  return (
    <div
      className="rounded-[28px] border-[3px] border-ink bg-white p-7 text-center"
      style={inkShadow}
    >
      <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
        Flow so far
      </p>
      <h2 className="mt-2 font-display text-[clamp(28px,5vw,40px)] font-black tracking-tight text-ink">
        {totals.correct} / {totals.total}
      </h2>
      {rows.length > 0 && (
        <div className="mx-auto mt-5 max-w-[420px] text-left">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
            By exercise
          </p>
          <ul className="mt-2 space-y-1.5">
            {rows.map((k) => (
              <li key={k} className="font-body text-[14px] text-ink-soft">
                <span className="font-bold text-ink">{KICKER[k]}</span>
                {" — "}
                {perExercise[k].correct} / {perExercise[k].done}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-7 flex items-center justify-center gap-5">
        <button
          type="button"
          onClick={onKeepGoing}
          className="btn-3d inline-flex items-center rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-white"
          style={redShadow}
        >
          Keep going
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

// ─── Round picker (FLOW-005) ─────────────────────────────────────────────
// The pre-start screen: same visual language as PartnerScreen/TopicScreen
// (kicker heading, btn-3d cards, ink borders) — 10 / 20 / ∞ preset cards
// plus a custom 1–50 field, one primary Start button. Round buffers may
// already be prefetching in the background while this is up; nothing gets
// dealt until Start fires.
function RoundPicker({
  presetChoice,
  customText,
  onPickPreset,
  onCustomChange,
  onStart,
}: {
  presetChoice: RoundPreset;
  customText: string;
  onPickPreset: (preset: RoundPreset) => void;
  onCustomChange: (raw: string) => void;
  onStart: () => void;
}) {
  const customActive = customText !== "";
  return (
    <div className="rise-in">
      <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
        Flow
      </p>
      <h1 className="mt-3 font-display text-[clamp(28px,4.6vw,44px)] font-black leading-[1.03] tracking-tight text-ink">
        How many exercises?
      </h1>
      <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
        Pick a round length, or go endless — you can always stop early with
        Finish.
      </p>

      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        {ROUND_PRESETS.map(({ key, label }) => {
          const selected = !customActive && presetChoice === key;
          return (
            <button
              key={key}
              type="button"
              aria-pressed={selected}
              onClick={() => onPickPreset(key)}
              className={`btn-3d rounded-3xl border-[3px] border-ink px-6 py-6 text-center font-display text-[28px] font-black transition ${
                selected
                  ? "bg-ink text-white"
                  : "bg-white text-ink hover:bg-paper-warm"
              }`}
              style={inkShadow}
            >
              {label}
            </button>
          );
        })}
      </div>

      <div className="mt-7 flex items-center gap-3">
        <label
          htmlFor="flow-custom-rounds"
          className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted"
        >
          Or a custom number
        </label>
        <input
          id="flow-custom-rounds"
          type="text"
          inputMode="numeric"
          value={customText}
          onChange={(e) => onCustomChange(e.target.value)}
          placeholder="1–50"
          className="w-24 rounded-2xl border-[3px] border-ink bg-white px-4 py-2 text-center font-body text-[16px] text-ink placeholder:text-ink-muted focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
        />
      </div>

      <div className="mt-9">
        <button
          type="button"
          onClick={onStart}
          className="btn-3d inline-flex items-center gap-2 rounded-2xl border-[3px] border-flag-red-deep bg-flag-red px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-white"
          style={redShadow}
        >
          Start
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-4 w-4"
          >
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </button>
      </div>
    </div>
  );
}
