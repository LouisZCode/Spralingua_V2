"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import { playSound } from "./shared/sound";
import { loadError } from "./shared/copy";
import { SATZ_ATTEMPT_COST } from "@/lib/coins";
import { useCoinBalance, useCoinsBypassed } from "./shared/Coins";
import SoundToggle from "./shared/SoundToggle";
import ThemeToggle from "./shared/ThemeToggle";

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

import FaelleTrainer from "./faelle/FaelleTrainer";
import {
  fetchRound as fetchFaelleRound,
  submitAttempt as submitFaelleAttempt,
  giveUp as giveUpFaelle,
  type CaseItem,
  type CaseVerdict,
} from "./faelle/api";
import AppHeader from "@/components/shared/AppHeader";

import SatzbauTrainer from "./satzbau/SatzbauTrainer";
import {
  fetchRound as fetchSatzbauRound,
  submitAttempt as submitSatzbauAttempt,
  giveUp as giveUpSatzbau,
  type ClauseItem,
  type ClauseVerdict,
} from "./satzbau/api";

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
  giveUpArticle as giveUpGenusArticle,
  type Article as GenusArticle,
  type ArticleVerdict as GenusArticleVerdict,
  type EndingSheet,
  type GenusItem,
} from "./genus/api";

import SprechenTrainer from "./sprechen/SprechenTrainer";
import {
  fetchRound as fetchSprechenRound,
  submitAttempt as submitSprechenAttempt,
  giveUp as giveUpSprechen,
  type SpokenTask,
  type SprechenVerdict,
} from "./sprechen/api";

import SzenarioTrainer from "./szenario/SzenarioTrainer";
import {
  fetchSzenarioRound,
  submitAttempt as submitSzenarioAttempt,
  type SzenarioRoundItem,
  type StructureResult,
} from "./szenario/api";
import { expectedTier, foldSeen, readSeen, writeSeen, type Tier } from "./szenario/seen";

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

import { postModeComplete } from "./development/api";

const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// FLOW-001/FLOW-006: the nine drills this mode draws from, plus Szenario
// (FLOW-006) as a capped tenth source — Tandem and Conversation Practice are
// deliberately not in the rotation. Genus deals its DRAG BEAT only (the
// gender choice); the typed production stays a standalone-page exercise.
type SourceKind =
  | "satz"
  | "verbformen"
  | "bauteil"
  | "verbindungen"
  | "zeitfaerbung"
  | "sprechen"
  | "genus"
  | "faelle"
  | "satzbau"
  | "szenario";

const ALL_KINDS: SourceKind[] = [
  "satz",
  "verbformen",
  "bauteil",
  "verbindungen",
  "zeitfaerbung",
  "sprechen",
  "genus",
  "faelle",
  "satzbau",
  "szenario",
];

const KICKER: Record<SourceKind, string> = {
  satz: "WORTSCHATZ",
  verbformen: "VERBFORMEN",
  bauteil: "BAUTEIL",
  verbindungen: "VERBINDUNGEN",
  zeitfaerbung: "ZEITFÄRBUNG",
  sprechen: "SPRECHEN",
  genus: "ARTIKEL",
  faelle: "FÄLLE",
  satzbau: "SATZBAU",
  szenario: "SZENARIO · STRUKTUR",
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
  | { kind: "faelle"; key: number; item: CaseItem }
  | { kind: "satzbau"; key: number; item: ClauseItem }
  | { kind: "szenario"; key: number; item: SzenarioRoundItem }
  | { kind: "satz"; key: number; card: DeckCard; rehearsal: boolean }
  | { kind: "verbformen"; key: number; card: DeckCard };

// FLOW-003: the only kicker that varies by more than source — a rehearsal
// satz deal says so plainly rather than presenting a write-free turn as an
// ordinary graded one. Everything else still reads straight off KICKER.
function kickerFor(deal: Deal): string {
  if (deal.kind === "satz" && deal.rehearsal)
    return "WORTSCHATZ · WIEDERHOLUNG";
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
      {/* DARK-003: this is the only bare, unbordered raven in the app rendered
          at hero scale (80px, alone in a 300px celebratory beat) — no card or
          badge fill behind it the way the header logo or the headline's
          circular waving-hello badge have, so it's the one that actually
          sinks into a dark surface. mascot-plate sits on the wrapper, not the
          image, so the halo stays put while mascot-hop/mascot-droop move the
          bird on top of it. */}
      <div className="mascot-plate flex h-28 w-28 items-center justify-center">
        <Image
          src="/mascot/raven.png"
          alt=""
          width={88}
          height={88}
          className={`mascot-keyline h-20 w-20 select-none ${
            mood === "happy" ? "mascot-hop" : mood === "sad" ? "mascot-droop" : ""
          }`}
        />
      </div>
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
// card's interval once the honest graded slice runs out.
type SatzCycle = {
  deck: DeckCard[];
  gradedOrder: string[];
  rehearsalOrder: string[];
};

// Due before new (not shuffled together like buildQueue) so an overdue
// review can never lose its priority slot to a lucky shuffle — Flow deals
// one card at a time over a long sitting, not one fixed round, so keeping
// due strictly first matters more here than in the standalone trainer.
function buildGradedOrder(deck: DeckCard[], newAllowance: number): string[] {
  const due = deck.filter((c) => c.srs.status === "due").map((c) => c.id);
  const fresh = deck.filter((c) => c.srs.status === "new").map((c) => c.id);
  return [...shuffle(due), ...shuffle(fresh).slice(0, newAllowance)];
}

function buildRehearsalOrder(deck: DeckCard[]): string[] {
  return shuffle(deck.map((c) => c.id));
}

// Returns the next satz card plus whether this deal is a write-free
// rehearsal turn. Drains gradedOrder first; once empty, deals from (and
// endlessly rebuilds) rehearsalOrder. An id can still be sitting in a stale
// order entry after its card left the deck mid-sitting (handleSatzRemove) —
// skip it rather than dealing a card that's gone.
function nextSatzCard(cycle: SatzCycle): [DeckCard, boolean] | null {
  const byId = new Map(cycle.deck.map((c) => [c.id, c] as const));
  while (cycle.gradedOrder.length > 0) {
    const id = cycle.gradedOrder.shift() as string;
    const card = byId.get(id);
    if (card) return [card, false];
  }
  if (cycle.rehearsalOrder.length === 0) {
    cycle.rehearsalOrder = buildRehearsalOrder(cycle.deck);
  }
  while (cycle.rehearsalOrder.length > 0) {
    const id = cycle.rehearsalOrder.shift() as string;
    const card = byId.get(id);
    if (card) return [card, true];
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
  faelle: CaseItem[];
  satzbau: ClauseItem[];
  szenario: SzenarioRoundItem[];
  satz: SatzCycle;
  verbformen: CardCycle;
  dealCounter: number;
  // FLOW-006: how many szenario items THIS round has already dealt — read
  // by pickSource against the round's cap (roundTarget/10) so szenario drops
  // out of the rotation once its quota is spent, even if its bag buffer still
  // holds prefetched items.
  szenarioDealtThisRound: number;
};

function emptyBag(): FlowBag {
  return {
    bauteil: [],
    verbindungen: [],
    zeitfaerbung: [],
    sprechen: [],
    genus: [],
    faelle: [],
    satzbau: [],
    szenario: [],
    satz: { deck: [], gradedOrder: [], rehearsalOrder: [] },
    verbformen: { deck: [], order: [] },
    dealCounter: 0,
    szenarioDealtThisRound: 0,
  };
}

function sourceCount(bag: FlowBag, kind: SourceKind): number {
  if (kind === "bauteil") return bag.bauteil.length;
  if (kind === "verbindungen") return bag.verbindungen.length;
  if (kind === "zeitfaerbung") return bag.zeitfaerbung.length;
  if (kind === "sprechen") return bag.sprechen.length;
  if (kind === "genus") return bag.genus.length;
  if (kind === "faelle") return bag.faelle.length;
  if (kind === "satzbau") return bag.satzbau.length;
  if (kind === "szenario") return bag.szenario.length;
  if (kind === "satz") return bag.satz.deck.length;
  return bag.verbformen.deck.length;
}

// A random exercise different from the previous one — unless only one
// source is left standing, in which case there's nothing to vary.
// FLOW-006: `szenarioCap` is this round's szenario quota (roundTarget/10,
// floored) — szenario drops out of `avail` once it's spent, same as any
// other exhausted source, just capped rather than buffer-driven.
function pickSource(
  bag: FlowBag,
  prevKind: SourceKind | null,
  szenarioCap: number,
): SourceKind | null {
  const avail = ALL_KINDS.filter((k) => {
    if (sourceCount(bag, k) === 0) return false;
    if (k === "szenario" && bag.szenarioDealtThisRound >= szenarioCap) {
      return false;
    }
    return true;
  });
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
  if (kind === "faelle") {
    const item = bag.faelle.shift();
    return item ? { kind, key, item } : null;
  }
  if (kind === "satzbau") {
    const item = bag.satzbau.shift();
    return item ? { kind, key, item } : null;
  }
  if (kind === "szenario") {
    const item = bag.szenario.shift();
    if (item) bag.szenarioDealtThisRound += 1;
    return item ? { kind, key, item } : null;
  }
  if (kind === "satz") {
    const dealt = nextSatzCard(bag.satz);
    return dealt ? { kind, key, card: dealt[0], rehearsal: dealt[1] } : null;
  }
  const card = nextCard(bag.verbformen);
  return card ? { kind: "verbformen", key, card } : null;
}

// FLOW-006: fold one GET /szenario/round batch's "scenarioId:questionIndex"
// tokens into the served tier's stored seen list, sequentially — same
// contract the standalone Szenario page applies one draw at a time
// (Szenario.tsx's loadScenario): cycleReset REPLACES the list with just
// that item's own token, otherwise it's appended. `priorForExpected` is the
// seen list already read for the caller's tier GUESS — reused only if the
// server actually served that tier; otherwise re-read fresh for whatever
// tier it did serve (SZEN-007's "server is authoritative" rule).
function foldSzenarioSeen(
  tier: Tier,
  items: SzenarioRoundItem[],
  expected: Tier,
  priorForExpected: string[],
) {
  let running = tier === expected ? priorForExpected : readSeen(tier);
  for (const item of items) {
    running = foldSeen(running, item);
  }
  writeSeen(tier, running);
}

// Once a round buffer drops to <=1, top it up in the background — fire and
// forget, dedupe not needed (an overlapping refill just appends twice).
function refillIfLow(
  bag: FlowBag,
  kind:
    | "bauteil"
    | "verbindungen"
    | "zeitfaerbung"
    | "sprechen"
    | "genus"
    | "faelle"
    | "satzbau"
    | "szenario",
  token: string,
  // Only read for the "szenario" branch — the account-level bucket driving
  // its tier guess (SZEN-007). undefined for every other kind.
  szenarioLevel?: string | null,
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
  } else if (kind === "faelle" && bag.faelle.length <= 1) {
    fetchFaelleRound(token)
      .then((items) => {
        bag.faelle = [...bag.faelle, ...items];
      })
      .catch(() => {});
  } else if (kind === "satzbau" && bag.satzbau.length <= 1) {
    fetchSatzbauRound(token)
      .then((items) => {
        bag.satzbau = [...bag.satzbau, ...items];
      })
      .catch(() => {});
  } else if (kind === "szenario" && bag.szenario.length <= 1) {
    // FLOW-006: same per-tier "scenarioId:questionIndex" seen-token
    // contract as the standalone page (szenario/seen.ts) — read before the
    // draw, fold the batch's tokens in sequentially after.
    const expected = expectedTier(szenarioLevel);
    const seen = readSeen(expected);
    fetchSzenarioRound(token, seen, 3)
      .then(({ tier, items }) => {
        bag.szenario = [...bag.szenario, ...items];
        foldSzenarioSeen(tier, items, expected, seen);
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
    faelle: { done: 0, correct: 0 },
    satzbau: { done: 0, correct: 0 },
    szenario: { done: 0, correct: 0 },
  };
}

// FLOW-005: the pre-start round-length picker — 10 / 20 / 30 presets or a
// custom 1–50, persisted across visits. Presets and the custom field are
// mutually exclusive: a non-empty custom value always wins over whichever
// preset was last picked (see the `roundChoice` derivation in Flow()), and
// picking a preset clears the custom field. The two states are never
// reconciled imperatively — the derivation IS the mutual-exclusion rule.
type RoundPreset = "10" | "20" | "30";
type StoredRoundChoice = RoundPreset | number;

const ROUND_STORAGE_KEY = "flow-rounds-v1";

const ROUND_PRESETS: { key: RoundPreset; label: string; value: number }[] = [
  { key: "10", label: "10", value: 10 },
  { key: "20", label: "20", value: 20 },
  { key: "30", label: "30", value: 30 },
];

function clampRounds(n: number): number {
  return Math.min(50, Math.max(1, Math.trunc(n)));
}

function loadStoredRoundChoice(): StoredRoundChoice | null {
  try {
    const raw = localStorage.getItem(ROUND_STORAGE_KEY);
    if (raw === "10" || raw === "20" || raw === "30") return raw;
    if (raw) {
      const n = Number(raw);
      if (Number.isFinite(n)) return clampRounds(n);
    }
  } catch {}
  // PAY-005: no stored preference — the affordable default decides.
  return null;
}

function persistRoundChoice(choice: StoredRoundChoice) {
  try {
    localStorage.setItem(ROUND_STORAGE_KEY, String(choice));
  } catch {}
}

function targetFromChoice(choice: StoredRoundChoice): number {
  if (choice === "10") return 10;
  if (choice === "20") return 20;
  if (choice === "30") return 30;
  return choice;
}

// FLOW-001: endless mixed-practice mode — one item at a time, drawn randomly
// across the eight existing exercises, running until the learner hits Finish
// or (FLOW-005) a chosen round length is reached. This component is the
// auth-guarded page shell + the dealing/tally state; each existing trainer
// runs unmodified except for its opt-in `flow` prop.
export default function Flow() {
  // FLOW-006: `user` is read only for `user?.level` — the account-level
  // bucket szenario's tier guess is derived from (SZEN-007), same as the
  // standalone Szenario page.
  const { token, ready, user, expireSession } = useAuth();
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

  // PAY-005: the smallest preset renders until the balance resolves — the
  // affordable seed can then only ever move the default UP, never 30→10.
  const [presetChoice, setPresetChoice] = useState<RoundPreset>("10");
  const [customText, setCustomText] = useState<string>("");

  // PAY-005: seed the picker once the balance resolves — the stored choice
  // wins only while it is still affordable; otherwise the default is the
  // largest preset the account can actually buy. Developers seed
  // immediately at the largest preset through the bypass (same guard as
  // SatzschmiedePicker). Replaces the mount-only localStorage hydration —
  // same SSR-safe idiom, reading storage during render would mismatch the
  // server-rendered HTML.
  const bal = useCoinBalance();
  const bypassed = useCoinsBypassed();
  const affordableMax = bypassed
    ? 50
    : bal !== null
      ? Math.floor(bal.balance / SATZ_ATTEMPT_COST)
      : null;
  const [roundSeeded, setRoundSeeded] = useState(false);
  useEffect(() => {
    if (roundSeeded) return;
    if (affordableMax === null) return; // balance not resolved yet
    setRoundSeeded(true);
    const stored = loadStoredRoundChoice();
    const largest =
      [...ROUND_PRESETS].reverse().find((p) => p.value <= affordableMax) ??
      ROUND_PRESETS[0];
    const seed =
      stored !== null && targetFromChoice(stored) <= affordableMax
        ? stored
        : largest.key;
    if (typeof seed === "number") {
      setCustomText(String(seed));
    } else {
      setPresetChoice(seed);
    }
  }, [roundSeeded, affordableMax]);

  const customNumber = customText === "" ? null : Number(customText);
  const roundChoice: StoredRoundChoice = customNumber ?? presetChoice;
  const roundTarget = targetFromChoice(roundChoice);

  // PAY-006: the picker's own coin gate — mirrors SatzschmiedePicker's
  // cost/maxAffordable/effectiveMax/insufficient/disabled chain, adapted to
  // Flow's ROUND_PRESETS/targetFromChoice shape. Computed here (not inside
  // RoundPicker) since bal/bypassed are already resolved by the hooks above;
  // only the derived gate values cross the prop boundary, so the balance
  // isn't fetched a second time.
  const roundMaxAffordable = bal
    ? Math.floor(bal.balance / SATZ_ATTEMPT_COST)
    : 50;
  // PAY-002: developer bypass — never cap affordable to 0 for devs (balance
  // is frozen at 100 by design; clamping would lock Start for any preset).
  const roundEffectiveMax = bypassed ? 50 : Math.min(50, roundMaxAffordable);
  const roundCost = roundTarget * SATZ_ATTEMPT_COST;
  const roundInsufficient =
    !bypassed && bal !== null && bal.balance < roundCost;
  const roundDisabled =
    roundInsufficient || roundTarget < 1 || roundTarget > roundEffectiveMax;

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
      // FLOW-006: szenario's per-round cap — roundTarget/10 (floored).
      // roundTarget can't change once dealing has started (the picker
      // screen is gone by then), so recomputing it per deal is cheap and
      // always correct.
      const szenarioCap = Math.floor(roundTarget / 10);
      const kind = pickSource(bag, prevKind, szenarioCap);
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
          kind === "genus" ||
          kind === "faelle" ||
          kind === "satzbau" ||
          kind === "szenario")
      ) {
        refillIfLow(
          bag,
          kind,
          token,
          kind === "szenario" ? user?.level : undefined,
        );
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
    [token, user?.level, roundTarget],
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
    if (
      phase === "ready" &&
      screen === "playing" &&
      !startedDealingRef.current
    ) {
      startedDealingRef.current = true;
      dealNext(null);
    }
  }, [phase, screen, dealNext]);

  // Initial load: every source fetches independently. A source that fails
  // or comes back empty just drops out of the rotation (console-silent); an
  // expired token triggers the session-expiry modal (same policy as every other practice page). Only
  // when every single source is unavailable does the page show an error.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const bag = bagRef.current;
    // FLOW-006: same per-tier seen-token read the standalone page does
    // before its first draw (SZEN-007) — read once up front so the initial
    // fetch and its seen-list fold below agree on the same snapshot.
    const szenarioExpected = expectedTier(user?.level);
    const szenarioSeen = readSeen(szenarioExpected);

    async function loadOne<T>(
      promise: Promise<T>,
      assign: (value: T) => void,
    ): Promise<void> {
      try {
        const value = await promise;
        if (!cancelled) assign(value);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          expireSession();
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
      loadOne(fetchFaelleRound(token), (items) => {
        bag.faelle = items;
      }),
      loadOne(fetchSatzbauRound(token), (items) => {
        bag.satzbau = items;
      }),
      loadOne(fetchSzenarioRound(token, szenarioSeen, 3), ({ tier, items }) => {
        bag.szenario = items;
        foldSzenarioSeen(tier, items, szenarioExpected, szenarioSeen);
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
  }, [token, expireSession, dealNext, user?.level]);

  const handleItemDone = useCallback(
    (kind: SourceKind, correct: boolean) => {
      setTotals((t) => ({
        total: t.total + 1,
        correct: t.correct + (correct ? 1 : 0),
      }));
      setPerExercise((p) => ({
        ...p,
        [kind]: {
          done: p[kind].done + 1,
          correct: p[kind].correct + (correct ? 1 : 0),
        },
      }));
      // GAME-001: deliberately silent. Every flow source already plays its own
      // win/fail earcon the moment it grades the attempt; this callback fires
      // later, when the learner presses Next, so playing here replayed the same
      // sound on a keypress that isn't an outcome at all.
      // FLOW-004: the outcome shapes the transition beat's mascot mood.
      dealNext(kind, correct ? "happy" : "sad");
    },
    [dealNext],
  );

  // GAME-001: the round-summary card is a bigger moment than any one item —
  // fires once per reveal (round target reached or manual Finish). The `else`
  // reset below is vestigial since PAY-002 retired "Keep going": nothing sets
  // `finished` back to false any more. Kept as a cheap guard rather than
  // assuming that stays true.
  //
  // The earcon follows the score: celebrating a 4/10 the same way as a 10/10
  // makes the celebration mean nothing. Below 60% the summary gets the warm
  // descending figure instead — informational, never a buzzer (see sound.ts).
  // A round finished without a single graded item has no outcome to sound.
  const finishedSoundRef = useRef(false);
  useEffect(() => {
    if (finished && !finishedSoundRef.current) {
      finishedSoundRef.current = true;
      if (totals.total > 0) {
        playSound(totals.correct / totals.total >= 0.6 ? "bigwin" : "bigfail");
      }
    } else if (!finished) {
      finishedSoundRef.current = false;
    }
  }, [finished, totals]);

  // GAME-001: ping the streak alongside the round-summary sound above — same
  // "reveal" moment, own ref so it can't interfere with the earcon's
  // double-fire guard. Same "no graded items, no outcome" rule as the sound:
  // a round finished without a single graded item has no outcome to credit
  // either. Same vestigial reset as the earcon above.
  const flowModePingedRef = useRef(false);
  useEffect(() => {
    if (finished && !flowModePingedRef.current) {
      flowModePingedRef.current = true;
      if (totals.total > 0 && token) {
        postModeComplete(token, "flow");
      }
    } else if (!finished) {
      flowModePingedRef.current = false;
    }
  }, [finished, totals, token]);

  // FLOW-005: auto-finish once the completed tally reaches the round's target
  // — same summary screen the manual Finish button shows. This was exact
  // equality while "Keep going" existed, so that continuing past the target
  // didn't bounce straight back to the summary; PAY-002 removed that path, so
  // >= is now the safer read of the same rule.
  useEffect(() => {
    if (totals.total >= roundTarget) {
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
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
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
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleVerbindungenAttempt = useCallback(
    async (itemId: string, answer: string): Promise<ChunkVerdict> => {
      if (!token) throw new UnauthorizedError("/verbindungen/attempts");
      try {
        return await submitVerbindungenAttempt(token, itemId, answer, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleVerbindungenGiveUp = useCallback(
    async (itemId: string): Promise<ChunkVerdict> => {
      if (!token) throw new UnauthorizedError("/verbindungen/attempts");
      try {
        return await giveUpVerbindungen(token, itemId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleFaelleAttempt = useCallback(
    async (itemId: string, answer: string): Promise<CaseVerdict> => {
      if (!token) throw new UnauthorizedError("/faelle/attempts");
      try {
        return await submitFaelleAttempt(token, itemId, answer, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleFaelleGiveUp = useCallback(
    async (itemId: string): Promise<CaseVerdict> => {
      if (!token) throw new UnauthorizedError("/faelle/attempts");
      try {
        return await giveUpFaelle(token, itemId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleSatzbauAttempt = useCallback(
    async (itemId: string, order: string[]): Promise<ClauseVerdict> => {
      if (!token) throw new UnauthorizedError("/satzbau/attempts");
      try {
        return await submitSatzbauAttempt(token, itemId, order, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleSatzbauGiveUp = useCallback(
    async (itemId: string): Promise<ClauseVerdict> => {
      if (!token) throw new UnauthorizedError("/satzbau/attempts");
      try {
        return await giveUpSatzbau(token, itemId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleZeitfaerbungAttempt = useCallback(
    async (itemId: string, answer: string): Promise<ZeitVerdict> => {
      if (!token) throw new UnauthorizedError("/zeitfaerbung/attempts");
      try {
        return await submitZeitfaerbungAttempt(token, itemId, answer, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleZeitfaerbungGiveUp = useCallback(
    async (itemId: string): Promise<ZeitVerdict> => {
      if (!token) throw new UnauthorizedError("/zeitfaerbung/attempts");
      try {
        return await giveUpZeitfaerbung(token, itemId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleSprechenAttempt = useCallback(
    async (taskId: string, audio: Blob): Promise<SprechenVerdict> => {
      if (!token) throw new UnauthorizedError("/sprechen/attempts");
      try {
        return await submitSprechenAttempt(token, taskId, audio, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleSprechenGiveUp = useCallback(
    async (taskId: string): Promise<SprechenVerdict> => {
      if (!token) throw new UnauthorizedError("/sprechen/give-up");
      try {
        return await giveUpSprechen(token, taskId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleSzenarioAttempt = useCallback(
    async (
      scenarioId: string,
      question: string,
      audio: Blob,
    ): Promise<StructureResult> => {
      if (!token) throw new UnauthorizedError("/szenario/attempts");
      try {
        return await submitSzenarioAttempt(
          token,
          scenarioId,
          question,
          audio,
          sid(),
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  // FLOW-006: szenario has no backend /give-up route yet — unlike bauteil,
  // verbindungen, zeitfaerbung, sprechen, genus and faelle above, it never
  // reaches the network. This resolves the same synthetic "gave up" verdict
  // every time: `overcomplicated` (a miss for the FLOW-006 tally) with
  // `gaveUp: true` so SzenarioTrainer renders its modest gave-up state
  // instead of the full breakdown. Nothing is written to the ledger or
  // drill_attempts here — a known v1 gap, worth a real endpoint (mirroring
  // sprechen/routes.py's give-up route) if this sees real use.
  const handleSzenarioGiveUp = useCallback(
    async (): Promise<StructureResult> => ({
      transcript: "",
      verdict: "overcomplicated",
      levelRead: "",
      coachMessage: "You gave up — no recording was judged.",
      sentences: [],
      skeleton: { kern: "", punkte: [], absprung: "", vokabelAnker: [] },
      gaveUp: true,
    }),
    [],
  );

  const handleGenusArticle = useCallback(
    async (
      itemId: string,
      article: GenusArticle,
    ): Promise<GenusArticleVerdict> => {
      if (!token) throw new UnauthorizedError("/genus/attempts");
      try {
        return await submitGenusArticle(token, itemId, article, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleGenusGiveUp = useCallback(
    async (itemId: string): Promise<GenusArticleVerdict> => {
      if (!token) throw new UnauthorizedError("/genus/attempts");
      try {
        return await giveUpGenusArticle(token, itemId, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleSatzAttempt = useCallback(
    async (
      cardId: string,
      audio: Blob,
      sessionId: string,
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
          satzRehearsalRef.current,
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession],
  );

  const handleVerbformenAttempt = useCallback(
    async (
      cardId: string,
      audio: Blob,
      sessionId: string,
    ): Promise<AttemptResult> => {
      if (!token) throw new UnauthorizedError("/verbformen/attempts");
      try {
        return await submitVerbformenAttempt(token, cardId, audio, sessionId);
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession],
  );

  const handleSatzRemove = useCallback(
    async (cardId: string): Promise<void> => {
      if (!token) return;
      try {
        await removeSatzCard(token, cardId);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          expireSession();
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
    [token, expireSession],
  );

  const handleVerbformenRemove = useCallback(
    async (cardId: string): Promise<void> => {
      if (!token) return;
      try {
        await removeVerbformenCard(token, cardId);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          expireSession();
          return;
        }
      }
      const bag = bagRef.current;
      bag.verbformen = {
        deck: bag.verbformen.deck.filter((c) => c.id !== cardId),
        order: bag.verbformen.order.filter((id) => id !== cardId),
      };
    },
    [token, expireSession],
  );

  const handleSatzReveal = useCallback(
    (cardId: string) => {
      if (!token) return;
      // FLOW-003: a rehearsal deal is write-free END TO END — the reveal
      // lapse (interval quartered, leech counter bumped) must not sneak in
      // through this side door while the graded attempt path is flagged off.
      if (satzRehearsalRef.current) return;
      revealSatzCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) expireSession();
      });
    },
    [token, expireSession],
  );

  // SATZ-010: a wrong gender pick — same fire-and-forget lapse policy as a
  // reveal. Wired only to the Satzschmiede deal, not Verbformen's verb-only one.
  const handleSatzGenderMiss = useCallback(
    (cardId: string) => {
      if (!token) return;
      // FLOW-003: same write-free guarantee as the reveal above.
      if (satzRehearsalRef.current) return;
      genderMissSatzCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) expireSession();
      });
    },
    [token, expireSession],
  );

  // The Artikel item's peekable "Endungen" sheet. GenusTrainer renders the
  // toggle whenever it HAS the sheet, but Flow never passed one — so the whole
  // premise of the exercise ("most endings give the gender away") had no way to
  // be looked up mid-stream, while the standalone page offers it one tap away.
  // Fetched once, on the first Artikel item dealt: a flow round that never
  // deals one shouldn't pay for the call.
  const [genusEndings, setGenusEndings] = useState<EndingSheet | null>(null);
  const genusEndingsAskedRef = useRef(false);
  useEffect(() => {
    if (!token || deal?.kind !== "genus" || genusEndingsAskedRef.current)
      return;
    genusEndingsAskedRef.current = true;
    fetchGenusMeta(token)
      .then((m) => setGenusEndings(m.endings))
      .catch((e) => {
        // Decorative — a missing sheet just means no toggle, as before.
        if (e instanceof UnauthorizedError) expireSession();
      });
  }, [token, deal, expireSession]);

  // SATZ-010 hint: Artikel-Anker's ending labels, fetched on first use.
  const handleSatzGenderCues = useCallback(async () => {
    if (!token) throw new UnauthorizedError("/genus/rules");
    try {
      return (await fetchGenusMeta(token)).endings;
    } catch (e) {
      if (e instanceof UnauthorizedError) expireSession();
      throw e;
    }
  }, [token, expireSession]);

  const handleVerbformenReveal = useCallback(
    (cardId: string) => {
      if (!token) return;
      revealVerbformenCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) expireSession();
      });
    },
    [token, expireSession],
  );

  const handleExplain = useCallback(
    async (
      cardId: string,
      transcript: string,
      corrected: string,
      error: string | null,
      sessionId?: string,
    ): Promise<string> => {
      if (!token) throw new UnauthorizedError("/satz/explain");
      try {
        return await explainAttempt(
          token,
          cardId,
          transcript,
          corrected,
          error,
          sessionId,
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession],
  );

  const handleFlag = useCallback(
    async (
      traceId: string,
      cardId: string | null,
      transcript: string,
      verdict: string,
      sessionId?: string,
    ): Promise<void> => {
      if (!token) throw new UnauthorizedError("/satz/flag");
      try {
        await flagVerdict(
          token,
          traceId,
          cardId,
          transcript,
          verdict,
          sessionId,
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession],
  );

  // UI-009: word-gloss popover wiring. Threaded into every trainer that
  // shows German prose the learner did not write: Verbindungen, Sprechen,
  // Fälle, Satzbau and both VocabTrainer mounts. Bauteil, Zeitfärbung and
  // Genus are mounted without it — their items are bare parts or single
  // words, so there is no sentence to gloss. Since MVP-001 the Flow is the
  // ONLY way a learner reaches Satzbau and Fälle, so a gloss those pages
  // have but the Flow doesn't pass through is a gloss nobody can use.
  // Same auth-guarded pattern as every handler above, filed under the
  // sitting's own OBS-007 session id (sid()) like every other Flow attempt.
  const handleGloss = useCallback(
    async (word: string, context: string): Promise<GlossInfo> => {
      if (!token) throw new UnauthorizedError("/satz/gloss");
      try {
        return await fetchGloss(token, word, context, sid());
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  const handleAddWord = useCallback(
    async (lemma: string): Promise<{ glossRemaining?: number } | void> => {
      if (!token) throw new UnauthorizedError("/satz/cards");
      try {
        // SATZ-013: gloss-popover add — counts against the daily gloss cap.
        const res = await addWord(token, lemma, sid(), "gloss");
        return { glossRemaining: res.glossRemaining };
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid],
  );

  if (!ready || !token) {
    return null;
  }

  return (
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      {/* APPHDR-001: shared header — the logo targets /practice, the
          signed-in learner's home. */}
      <AppHeader back={{ href: "/practice", label: "← Menu" }} />

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-12">
        {screen === "picker" && phase !== "error" ? (
          <RoundPicker
            presetChoice={presetChoice}
            customText={customText}
            onPickPreset={handlePickPreset}
            onCustomChange={handleCustomChange}
            onStart={handleStart}
            insufficient={roundInsufficient}
            disabled={roundDisabled}
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
                {loadError("this round")}
              </p>
            ) : finished ? (
              <SummaryCard totals={totals} perExercise={perExercise} />
            ) : (
              <>
                <div className="mb-4 flex items-center justify-between">
                  <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
                    {totals.total} item{totals.total === 1 ? "" : "s"} ·{" "}
                    {totals.correct} ✓ · {totals.total} / {roundTarget}
                  </p>
                  <div className="flex items-center gap-3">
                    <ThemeToggle />
                    <SoundToggle />
                    <button
                      type="button"
                      onClick={() => setFinished(true)}
                      className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-line bg-card px-5 py-2 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink"
                      style={inkShadow}
                    >
                      Finish
                    </button>
                  </div>
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
                        onFlowDone={(correct) =>
                          handleItemDone("bauteil", correct)
                        }
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
                        onFlowDone={(correct) =>
                          handleItemDone("sprechen", correct)
                        }
                        allowGiveUp
                        onGiveUp={handleSprechenGiveUp}
                        sessionId={sid()}
                      />
                    )}
                    {deal.kind === "szenario" && (
                      <SzenarioTrainer
                        key={deal.key}
                        scenario={deal.item}
                        initialPhase="scene"
                        onStart={noopNewRound}
                        onAttempt={handleSzenarioAttempt}
                        onNewQuestion={noopNewRound}
                        onGloss={handleGloss}
                        onAdd={handleAddWord}
                        flow
                        onFlowDone={(correct) =>
                          handleItemDone("szenario", correct)
                        }
                        allowGiveUp
                        onGiveUp={handleSzenarioGiveUp}
                        sessionId={sid()}
                      />
                    )}
                    {deal.kind === "genus" && (
                      <GenusTrainer
                        key={deal.key}
                        round={[deal.item]}
                        onArticle={handleGenusArticle}
                        onNewRound={noopNewRound}
                        flow
                        endings={genusEndings}
                        onFlowDone={(correct) =>
                          handleItemDone("genus", correct)
                        }
                        allowGiveUp
                        onGiveUp={handleGenusGiveUp}
                      />
                    )}
                    {deal.kind === "faelle" && (
                      <FaelleTrainer
                        key={deal.key}
                        round={[deal.item]}
                        onAttempt={handleFaelleAttempt}
                        onNewRound={noopNewRound}
                        onGloss={handleGloss}
                        onAdd={handleAddWord}
                        flow
                        onFlowDone={(correct) =>
                          handleItemDone("faelle", correct)
                        }
                        allowGiveUp
                        onGiveUp={handleFaelleGiveUp}
                      />
                    )}
                    {deal.kind === "satzbau" && (
                      <SatzbauTrainer
                        key={deal.key}
                        round={[deal.item]}
                        onAttempt={handleSatzbauAttempt}
                        onNewRound={noopNewRound}
                        onGloss={handleGloss}
                        onAdd={handleAddWord}
                        flow
                        onFlowDone={(correct) =>
                          handleItemDone("satzbau", correct)
                        }
                        allowGiveUp
                        onGiveUp={handleSatzbauGiveUp}
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
                        onFlowDone={(correct) =>
                          handleItemDone("satz", correct)
                        }
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

// PAY-002: no "Keep going". A round is a pre-paid length the learner chose on
// the picker, and extending it from its own summary made that number — and the
// coin cost printed next to it — mean nothing. Another round is a deliberate
// trip through the menu.
function SummaryCard({
  totals,
  perExercise,
}: {
  totals: { total: number; correct: number };
  perExercise: Record<SourceKind, Tally>;
}) {
  const rows = ALL_KINDS.filter((k) => perExercise[k].done > 0);
  return (
    <div
      className="rounded-[28px] border-[3px] border-line bg-card p-7 text-center"
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
      <div className="mt-7 flex items-center justify-center">
        <Link
          href="/practice"
          className="btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] border-line bg-card px-7 py-3.5 font-display text-[14px] font-black uppercase tracking-[0.16em] text-ink"
          style={inkShadow}
        >
          ← Back to menu
        </Link>
      </div>
    </div>
  );
}

// ─── Round picker (FLOW-005) ─────────────────────────────────────────────
// The pre-start screen: same visual language as PartnerScreen/TopicScreen
// (kicker heading, btn-3d cards, ink borders) — 10 / 20 / 30 preset cards
// plus a custom 1–50 field, one primary Start button. Round buffers may
// already be prefetching in the background while this is up; nothing gets
// dealt until Start fires.
function RoundPicker({
  presetChoice,
  customText,
  onPickPreset,
  onCustomChange,
  onStart,
  insufficient,
  disabled,
}: {
  presetChoice: RoundPreset;
  customText: string;
  onPickPreset: (preset: RoundPreset) => void;
  onCustomChange: (raw: string) => void;
  onStart: () => void;
  insufficient: boolean;
  disabled: boolean;
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
      {/* UI-014 (d): first-run framing — Flow has no empty-state onboarding
          of its own (unlike Satzschmiede, whose pool-building step covers
          this before a first-timer ever reaches a round picker), so this
          screen is the only place to say what a round actually deals. */}
      <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
        A mixed stream of quick grammar drills — endings, verb forms, cases,
        word order and more. Each one is pulled from what you keep getting
        wrong, and corrected the moment you miss it.
      </p>
      <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
        Pick a round length — you can always stop early with Finish.
      </p>

      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        {ROUND_PRESETS.map(({ key, label, value }) => {
          const selected = !customActive && presetChoice === key;
          return (
            <button
              key={key}
              type="button"
              aria-pressed={selected}
              onClick={() => onPickPreset(key)}
              className={`btn-3d rounded-3xl border-[3px] border-line px-6 py-6 text-center transition ${
                selected
                  ? "bg-ink-fill text-on-fill"
                  : "bg-card text-ink hover:bg-paper-warm"
              }`}
              style={inkShadow}
            >
              <span className="font-display text-[28px] font-black">
                {label}
              </span>
              <span className="mt-1 block font-body text-[11px] font-bold uppercase tracking-[0.14em] opacity-70">
                ≈ {value * SATZ_ATTEMPT_COST} coins
              </span>
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
          className="w-24 rounded-2xl border-[3px] border-line bg-card px-4 py-2 text-center font-body text-[16px] text-ink placeholder:text-ink-muted focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
        />
      </div>

      <div className="mt-9">
        <button
          type="button"
          onClick={onStart}
          disabled={disabled}
          className="btn-3d inline-flex items-center gap-2 rounded-2xl border-[3px] border-red-line bg-flag-red-fill px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-on-fill disabled:cursor-not-allowed disabled:opacity-40"
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
        {insufficient && (
          <p className="mt-2 font-body text-[13px] font-semibold text-flag-red-deep">
            Not enough coins for this round —{" "}
            <a href="/pricing" className="underline underline-offset-2">
              get more coins
            </a>{" "}
            or pick a smaller round.
          </p>
        )}
      </div>
    </div>
  );
}
