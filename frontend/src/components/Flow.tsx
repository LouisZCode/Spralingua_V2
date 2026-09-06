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
  type GenusPool,
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
  giveUpAttempt as giveUpSzenario,
  type SzenarioRoundItem,
  type StructureResult,
} from "./szenario/api";
import { expectedTier, foldSeen, readSeen, writeSeen, type Tier } from "./szenario/seen";

import VocabTrainer from "./satzschmiede/VocabTrainer";
import {
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

// FLOW-001/FLOW-006: the eight drills this mode draws from, plus Szenario
// (FLOW-006) as a capped ninth source — Tandem and Conversation Practice are
// deliberately not in the rotation. Genus deals its DRAG BEAT only (the
// gender choice); the typed production stays a standalone-page exercise.
// PRODUCT-003 (2026-09-05): Satzschmiede (satz) deals left the rotation —
// Satzschmiede has its own /practice card now, so the Flow only carries
// exercises surfaced nowhere else. Verbformen deals stay (own deck,
// VocabTrainer only renders them).
type SourceKind =
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
// FLOW-007: the six typed-drill items carry an optional `retry` flag — set
// when this deal is a Flow-level re-serve of a missed item (see
// `missedRef`/`buildRetryDeal` below). sprechen/szenario/verbformen keep
// their own opt-in retry and never enter that queue, so their item types are
// unchanged.
type Deal =
  | { kind: "bauteil"; key: number; item: BauteilItem & { retry?: boolean } }
  | {
      kind: "verbindungen";
      key: number;
      item: ChunkItem & { retry?: boolean };
    }
  | { kind: "zeitfaerbung"; key: number; item: ZeitItem & { retry?: boolean } }
  | { kind: "sprechen"; key: number; item: SpokenTask }
  | { kind: "genus"; key: number; item: GenusItem & { retry?: boolean } }
  | { kind: "faelle"; key: number; item: CaseItem & { retry?: boolean } }
  | { kind: "satzbau"; key: number; item: ClauseItem & { retry?: boolean } }
  | { kind: "szenario"; key: number; item: SzenarioRoundItem }
  | { kind: "verbformen"; key: number; card: DeckCard };

// FLOW-007: one pending miss awaiting its single retry slot. Scoped to the
// six typed drills that already understand a local `retry: true` item flag
// (their own re-queue is disabled in Flow mode — see `dealNext` below, which
// re-implements it one level up since Flow, not the trainer, owns
// sequencing).
type RetryKind =
  | "bauteil"
  | "verbindungen"
  | "zeitfaerbung"
  | "genus"
  | "faelle"
  | "satzbau";

type MissedEntry =
  | { kind: "bauteil"; item: BauteilItem }
  | { kind: "verbindungen"; item: ChunkItem }
  | { kind: "zeitfaerbung"; item: ZeitItem }
  | { kind: "genus"; item: GenusItem }
  | { kind: "faelle"; item: CaseItem }
  | { kind: "satzbau"; item: ClauseItem };

// The shape `deal.item` actually has for the six retriable kinds — the same
// per-kind `& { retry?: boolean }` extension `Deal` carries, so
// `handleItemDone` can read `item.retry` (to cap re-queue depth at 1)
// without a cast.
type FlowRetryItem =
  | (BauteilItem & { retry?: boolean })
  | (ChunkItem & { retry?: boolean })
  | (ZeitItem & { retry?: boolean })
  | (GenusItem & { retry?: boolean })
  | (CaseItem & { retry?: boolean })
  | (ClauseItem & { retry?: boolean });

const RETRY_KINDS: ReadonlySet<SourceKind> = new Set<RetryKind>([
  "bauteil",
  "verbindungen",
  "zeitfaerbung",
  "genus",
  "faelle",
  "satzbau",
]);

function isRetryKind(kind: SourceKind): kind is RetryKind {
  return RETRY_KINDS.has(kind);
}

// Re-serve a pending miss as a fresh dealt turn, marked `retry: true` so the
// six typed drills' own item-header code (`item.retry ? " · second try" :
// ""`) would treat it as a second chance — though that code is gated behind
// `!flow` and never renders in Flow mode, which is why Flow grows its own
// small "Noch einmal" kicker instead (see the render below).
function buildRetryDeal(entry: MissedEntry, key: number): Deal {
  switch (entry.kind) {
    case "bauteil":
      return { kind: "bauteil", key, item: { ...entry.item, retry: true } };
    case "verbindungen":
      return {
        kind: "verbindungen",
        key,
        item: { ...entry.item, retry: true },
      };
    case "zeitfaerbung":
      return {
        kind: "zeitfaerbung",
        key,
        item: { ...entry.item, retry: true },
      };
    case "genus":
      return { kind: "genus", key, item: { ...entry.item, retry: true } };
    case "faelle":
      return { kind: "faelle", key, item: { ...entry.item, retry: true } };
    case "satzbau":
      return { kind: "satzbau", key, item: { ...entry.item, retry: true } };
  }
}

// True only for a dealt turn that is itself a FLOW-007 retry re-serve — used
// to show the "Noch einmal" kicker. sprechen/szenario/verbformen can never
// be true here (they're not in `RetryKind`).
function isRetryDeal(deal: Deal): boolean {
  switch (deal.kind) {
    case "bauteil":
    case "verbindungen":
    case "zeitfaerbung":
    case "genus":
    case "faelle":
    case "satzbau":
      return deal.item.retry === true;
    default:
      return false;
  }
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

// Verbformen's endless cycle — due, then new, then later, shuffled within
// each tier, rebuilt from the whole deck once exhausted, every deal fully
// graded.
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
  // PRODUCT-011: only read for the "genus" branch — this sitting's rotated
  // pool id (chosen once at sitting start, see `nextGenusPool` below), so a
  // mid-sitting refill draws from the SAME themed pool the sitting started
  // with rather than silently reverting to `basis`.
  genusPool?: string | null,
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
    // PRODUCT-011: refill from this SITTING's pool (fixed at sitting start),
    // never a fresh rotation pick mid-sitting.
    fetchGenusRound(token, genusPool ?? undefined, GENUS_PERSONAL_MAX)
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

// PRODUCT-011 (2026-09-06): the Flow silently served the `basis` Genus pool
// on every sitting (it passed no `pool` at all) — the one gap the
// 2026-08-28 content audit found. Now each sitting advances to the NEXT
// curated pool in `GET /genus/rules`' display order, one step past whichever
// pool the LAST sitting used (persisted here, not per-refill), so a learner
// tours the whole catalog over successive sittings instead of only ever
// seeing `basis`. `GENUS_PERSONAL_MAX` (3, down from the standalone page's
// default 5) keeps the deck half from crowding out the themed pool the
// rotation is trying to surface.
const GENUS_POOL_STORAGE_KEY = "flow-genus-pool-v1";
const GENUS_PERSONAL_MAX = 3;

function nextGenusPool(pools: GenusPool[]): string {
  if (pools.length === 0) return "basis"; // defensive only — get_round degrades an unknown/empty pool id to basis itself
  let lastId: string | null = null;
  try {
    lastId = localStorage.getItem(GENUS_POOL_STORAGE_KEY);
  } catch {}
  // Unset (first-ever sitting) or stale (a pool renamed/removed since the
  // last sitting) both fall back to the first pool in display order, not an
  // error — same "degrade, never fail the round" posture as the backend's
  // own unknown-pool handling.
  const idx = lastId ? pools.findIndex((p) => p.id === lastId) : -1;
  return (idx === -1 ? pools[0] : pools[(idx + 1) % pools.length]).id;
}

function persistGenusPool(id: string) {
  try {
    localStorage.setItem(GENUS_POOL_STORAGE_KEY, id);
  } catch {}
}

// FLOW-007: `skipped` counts a deliberate Skip on this exercise — a slot
// that consumed the round without ever being graded, tracked separately
// from `done`/`correct` so the "By exercise" list's accuracy math is
// unaffected by skips.
type Tally = { done: number; correct: number; skipped: number };

function emptyTallies(): Record<SourceKind, Tally> {
  return {
    verbformen: { done: 0, correct: 0, skipped: 0 },
    bauteil: { done: 0, correct: 0, skipped: 0 },
    verbindungen: { done: 0, correct: 0, skipped: 0 },
    zeitfaerbung: { done: 0, correct: 0, skipped: 0 },
    sprechen: { done: 0, correct: 0, skipped: 0 },
    genus: { done: 0, correct: 0, skipped: 0 },
    faelle: { done: 0, correct: 0, skipped: 0 },
    satzbau: { done: 0, correct: 0, skipped: 0 },
    szenario: { done: 0, correct: 0, skipped: 0 },
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
  const [totals, setTotals] = useState({ total: 0, correct: 0, skipped: 0 });
  const [perExercise, setPerExercise] =
    useState<Record<SourceKind, Tally>>(emptyTallies());

  const bagRef = useRef<FlowBag>(emptyBag());
  // PRODUCT-011: this sitting's rotated Genus pool, fixed once at sitting
  // start (the initial-load effect below) and read by every mid-sitting
  // refill — never recomputed per refill, or one sitting would tour several
  // pools instead of one. `genusPoolChosenRef` guards the CHOICE itself
  // (see the initial-load effect's comment) against the effect re-running
  // more than once for the same mount.
  const genusPoolRef = useRef<string | null>(null);
  const genusPoolChosenRef = useRef(false);

  // FLOW-007: pending misses (six typed drills only) awaiting their single
  // retry slot, and the set of deal keys already resolved (by a graded
  // finish OR a Skip) — the guard a stale async callback (an attempt that
  // resolves after the learner has already skipped past it, or a doubled
  // click) checks before touching totals/perExercise/missedRef a second
  // time for the same slot.
  const missedRef = useRef<MissedEntry[]>([]);
  const settledDealKeysRef = useRef<Set<number>>(new Set());

  // FLOW-007 review fix (BLOCKER 1): every typed drill stays mounted after
  // Check/Give-up, showing its own verdict + its own Next — a Check/Give-up
  // has already spent a coin and written a drill_attempts/coin_ledger row by
  // then, even though the trainer hasn't called onFlowDone yet. Skip must
  // know this or it silently turns an already-graded item into a free skip
  // (LEDGER-002/PAY-002 both assume grading and skipping are mutually
  // exclusive events). `gradedRef` is the verdict for whichever deal is
  // CURRENTLY mounted; `dealGraded` mirrors its presence in React state
  // purely so the control's label ("Skip" vs "Next →") re-renders — the
  // handlers below read the ref, not the state, to avoid any stale-closure
  // risk. `currentDealKeyRef` lets the attempt/give-up handlers (which don't
  // otherwise see `deal`) find the live deal key and ignore a response that
  // resolves after the round has already moved on — the same stale-guard
  // idea as `settledDealKeysRef`.
  type Graded = { dealKey: number; correct: boolean };
  const gradedRef = useRef<Graded | null>(null);
  const [dealGraded, setDealGraded] = useState(false);
  const currentDealKeyRef = useRef<number | null>(null);

  // Overwrite semantics: the latest grading event for a deal IS the verdict
  // Skip should honor. Correct for the five typed drills below (Check/Give-up
  // is a one-shot verdict per deal — the form disappears once `verdict` is
  // set) and for sprechen/szenario/verbformen (verbformen's own "Try again"
  // genuinely resets its local "revealed" flag before a fresh attempt, so a
  // later correct attempt really does supersede an earlier miss).
  const recordGraded = useCallback((dealKey: number, correct: boolean) => {
    gradedRef.current = { dealKey, correct };
    setDealGraded(true);
  }, []);

  // Genus only: GenusTrainer's `slippedRef` latches true on the FIRST wrong
  // drag (or a give-up) and is never cleared until the deal itself ends — a
  // later correct drag on the SAME item does not un-slip it. Mirror that
  // latch here: once a deal is marked incorrect, a later correct call for
  // the same deal key must not flip it back to true.
  const recordGenusGraded = useCallback((dealKey: number, correct: boolean) => {
    gradedRef.current =
      gradedRef.current && gradedRef.current.dealKey === dealKey
        ? { dealKey, correct: gradedRef.current.correct && correct }
        : { dealKey, correct };
    setDealGraded(true);
  }, []);

  // Reset per deal — a fresh mount has nothing graded yet.
  useEffect(() => {
    currentDealKeyRef.current = deal?.key ?? null;
    gradedRef.current = null;
    setDealGraded(false);
  }, [deal]);

  // OBS-007: one Langfuse practice-session id for the whole sitting, minted
  // lazily on first use — every attempt across every exercise threads it, so
  // the sitting groups as one session.
  const sessionIdRef = useRef<string | null>(null);
  const sid = useCallback((): string => {
    sessionIdRef.current ??= "flow-" + crypto.randomUUID().replace(/-/g, "");
    return sessionIdRef.current;
  }, []);

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
      // FLOW-007: before pulling a fresh item for `kind`, check for a
      // pending retry of that SAME kind. Substituting it REPLACES this
      // slot's fresh pull rather than adding one, so roundTarget/totals need
      // no change. Three guards: past the round's midpoint (so the retry
      // isn't served too soon after the miss), never on the round's last
      // slot (so it never dead-ends unresolved), and `kind !== prevKind`
      // (review fix, SHOULD-FIX 2) — pickSource only excludes the previous
      // deal's kind when more than one source is available, so when the
      // miss's kind is the ONLY source left standing, `kind` can equal
      // `prevKind` and the retry would otherwise land immediately after its
      // own miss. `bag.dealCounter` is this round's 0-indexed count of
      // deals served so far (both dealFromSource and the retry branch below
      // increment it once per slot), so `+1` is the 1-indexed slot about to
      // be filled.
      const dealNumber = bag.dealCounter + 1;
      const retryEligible =
        dealNumber > roundTarget / 2 && dealNumber < roundTarget;
      let next: Deal | null = null;
      if (retryEligible && kind !== prevKind) {
        const idx = missedRef.current.findIndex((m) => m.kind === kind);
        if (idx !== -1) {
          const missed = missedRef.current[idx];
          missedRef.current.splice(idx, 1);
          next = buildRetryDeal(missed, ++bag.dealCounter);
        }
      }
      if (!next) {
        next = dealFromSource(bag, kind);
      }
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
          kind === "genus" ? genusPoolRef.current : undefined,
        );
      }
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

    // PRODUCT-011: this sitting's Genus pool is chosen here, once, from
    // `/genus/rules`' display order — not passed to `loadOne` like the other
    // sources because the round fetch below depends on it. Persisted right
    // after it's picked (not after the round fetch succeeds) so the next
    // sitting still advances even if this sitting's round fetch itself later
    // fails.
    //
    // This effect is NOT guaranteed to run only once per mount: dev's React
    // Strict Mode double-invokes it (harmless — see below). It USED TO also
    // re-run in PRODUCTION, doubling every one of the nine sources'
    // `/round`/`/deck` fetches per sitting: PAY-005's picker seeds
    // `roundTarget` to the smallest preset before the coin balance resolves
    // and then updates it once the balance is known, which changed
    // `dealNext`'s identity (its `useCallback` deps include `roundTarget`)
    // — and `dealNext` sat in THIS effect's own dependency array below even
    // though nothing in this effect's body ever calls it (a leftover from
    // before FLOW-005 split first-deal firing into its own effect above).
    // FLOW-008 (2026-09-06, from the genusrot review's production
    // measurement — `wave4/genusrotrev/prodcount_results.json`, all nine
    // sources fetched exactly twice) fixed this by dropping `dealNext` from
    // the deps array; see the closing line below. Refetching is harmless for
    // the other 8 sources (a fresh random round is as good as the one it
    // replaces), but choosing the NEXT pool is a stateful, one-way step —
    // rerunning it would silently skip a pool every time this effect
    // re-executes. `genusPoolChosenRef` (defined with the other per-mount
    // refs above) makes the rotation choice itself run at most once per true
    // component mount no matter how many times this effect body
    // re-executes (Strict Mode's dev-only double-invoke, now the only
    // remaining trigger): whichever invocation gets there first rotates and
    // persists; every later one (this mount only) just reuses
    // `genusPoolRef.current` for its own round refetch.
    // Takes `tok` as a parameter (rather than closing over the outer
    // `token`) so it type-checks as `string` — a nested function declaration
    // doesn't inherit the `if (!token) return;` narrowing above the way a
    // same-scope expression like `fetchBauteilRound(token)` below does.
    async function loadGenusForSitting(tok: string): Promise<void> {
      try {
        let pool: string;
        if (!genusPoolChosenRef.current) {
          const meta = await fetchGenusMeta(tok);
          if (cancelled) return;
          pool = nextGenusPool(meta.pools);
          genusPoolChosenRef.current = true;
          genusPoolRef.current = pool;
          persistGenusPool(pool);
        } else {
          pool = genusPoolRef.current ?? "basis";
        }
        const items = await fetchGenusRound(tok, pool, GENUS_PERSONAL_MAX);
        if (!cancelled) bag.genus = items;
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
      loadGenusForSitting(token),
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
    // FLOW-008: `dealNext` deliberately excluded — it is never called from
    // this effect's body (see the comment above this effect for the full
    // story); including it re-ran this entire nine-source prefetch every
    // time PAY-005's balance-driven `roundTarget` update changed `dealNext`'s
    // identity, which a production build doubled every single time.
  }, [token, expireSession, user?.level]);

  const handleItemDone = useCallback(
    (
      kind: SourceKind,
      correct: boolean,
      dealKey: number,
      item?: FlowRetryItem,
    ) => {
      // FLOW-007: ignore a stale callback for a slot already resolved (by
      // this same call landing twice, or by a Skip that already moved past
      // it while an attempt/give-up was in flight).
      if (settledDealKeysRef.current.has(dealKey)) return;
      settledDealKeysRef.current.add(dealKey);
      setTotals((t) => ({
        ...t,
        total: t.total + 1,
        correct: t.correct + (correct ? 1 : 0),
      }));
      setPerExercise((p) => ({
        ...p,
        [kind]: {
          ...p[kind],
          done: p[kind].done + 1,
          correct: p[kind].correct + (correct ? 1 : 0),
        },
      }));
      // FLOW-007: queue exactly one retry for a genuine miss on the six
      // typed drills — capped at depth 1, matching the standalone trainers'
      // own `!item.retry` guard (a retry missed again is dropped, not
      // requeued a third time).
      if (!correct && item && isRetryKind(kind) && !item.retry) {
        missedRef.current.push({ kind, item } as MissedEntry);
      }
      // GAME-001: deliberately silent. Every flow source already plays its own
      // win/fail earcon the moment it grades the attempt; this callback fires
      // later, when the learner presses Next, so playing here replayed the same
      // sound on a keypress that isn't an outcome at all.
      // FLOW-004: the outcome shapes the transition beat's mascot mood.
      dealNext(kind, correct ? "happy" : "sad");
    },
    [dealNext],
  );

  // FLOW-007 (Luis: "we need to add an option to skip also, in case they are
  // getting stuck, or the program is not working correctly"): a quiet,
  // always-available escape that lives in Flow's own chrome, outside the
  // trainer, so it works even if the mounted trainer is itself broken (a
  // failed fetch, a stuck request) and is never gated by a trainer-local
  // busy/arming flag. No attempt or give-up is posted — no coin charge, no
  // ledger row — but the slot IS consumed: the round advances exactly like a
  // graded deal (the honest reading of "a round ends where it ends" — the
  // learner spent the slot on purpose). A skipped retry is simply dropped,
  // never re-queued. Guarded by the same settledDealKeysRef as
  // handleItemDone so a request already in flight when Skip is pressed can't
  // double-count this slot when it eventually resolves.
  const handleSkip = useCallback(() => {
    const current = deal;
    if (!current) return;
    if (settledDealKeysRef.current.has(current.key)) return;
    // FLOW-007 review fix (BLOCKER 1): the trainer already has a verdict for
    // this exact deal (Check/Give-up already ran — coin spent, ledger row
    // already written) even though it hasn't pressed its own Next yet. Skip
    // must not silently discard that: run the exact accounting a real Next
    // press would (queues the retry on a miss, counts a correct, never
    // touches `totals.skipped`), then advance exactly like handleItemDone
    // does. handleItemDone owns the settledDealKeysRef marking here.
    if (gradedRef.current && gradedRef.current.dealKey === current.key) {
      const { correct } = gradedRef.current;
      switch (current.kind) {
        case "bauteil":
        case "verbindungen":
        case "zeitfaerbung":
        case "genus":
        case "faelle":
        case "satzbau":
          handleItemDone(current.kind, correct, current.key, current.item);
          break;
        default:
          handleItemDone(current.kind, correct, current.key);
      }
      return;
    }
    settledDealKeysRef.current.add(current.key);
    setTotals((t) => ({ ...t, total: t.total + 1, skipped: t.skipped + 1 }));
    setPerExercise((p) => ({
      ...p,
      [current.kind]: { ...p[current.kind], skipped: p[current.kind].skipped + 1 },
    }));
    dealNext(current.kind, "neutral");
  }, [deal, dealNext, handleItemDone]);

  // GAME-001: the round-summary card is a bigger moment than any one item —
  // fires once per reveal (round target reached or manual Finish). The `else`
  // reset below is vestigial since PAY-002 retired "Keep going": nothing sets
  // `finished` back to false any more. Kept as a cheap guard rather than
  // assuming that stays true.
  //
  // The earcon follows the score: celebrating a 4/10 the same way as a 10/10
  // makes the celebration mean nothing. Below 60% the summary gets the warm
  // descending figure instead — informational, never a buzzer (see sound.ts).
  // A round finished without a single GRADED item has no outcome to sound —
  // FLOW-007: `totals.total` now also counts skips, so the graded count is
  // `total - skipped`, not `total` on its own (a skip-only round would
  // otherwise sound a "bigfail" for a round that graded nothing).
  const finishedSoundRef = useRef(false);
  useEffect(() => {
    if (finished && !finishedSoundRef.current) {
      finishedSoundRef.current = true;
      const graded = totals.total - totals.skipped;
      if (graded > 0) {
        playSound(totals.correct / graded >= 0.6 ? "bigwin" : "bigfail");
      }
    } else if (!finished) {
      finishedSoundRef.current = false;
    }
  }, [finished, totals]);

  // GAME-001: ping the streak alongside the round-summary sound above — same
  // "reveal" moment, own ref so it can't interfere with the earcon's
  // double-fire guard. Same "no graded items, no outcome" rule as the sound:
  // a round finished without a single graded item has no outcome to credit
  // either — FLOW-007: a round that was entirely skips must not POST
  // /streak/mode at all (the backend's own has_attempt_today anti-spoof
  // check would no-op it anyway, since a skip never writes a drill_attempts
  // row, but there's no reason to make the call). Same vestigial reset as
  // the earcon above.
  const flowModePingedRef = useRef(false);
  useEffect(() => {
    if (finished && !flowModePingedRef.current) {
      flowModePingedRef.current = true;
      const graded = totals.total - totals.skipped;
      if (graded > 0 && token) {
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
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await submitBauteilAttempt(token, itemId, answer, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  // FLOW-002: the deliberate "give up" escape — same auth-guarded shape as
  // the attempt handlers above, one per item drill (verbformen keeps its
  // existing onReveal instead; genus wires only its drag beat).
  const handleBauteilGiveUp = useCallback(
    async (itemId: string): Promise<BauteilVerdict> => {
      if (!token) throw new UnauthorizedError("/bauteil/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await giveUpBauteil(token, itemId, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleVerbindungenAttempt = useCallback(
    async (itemId: string, answer: string): Promise<ChunkVerdict> => {
      if (!token) throw new UnauthorizedError("/verbindungen/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await submitVerbindungenAttempt(token, itemId, answer, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleVerbindungenGiveUp = useCallback(
    async (itemId: string): Promise<ChunkVerdict> => {
      if (!token) throw new UnauthorizedError("/verbindungen/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await giveUpVerbindungen(token, itemId, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleFaelleAttempt = useCallback(
    async (itemId: string, answer: string): Promise<CaseVerdict> => {
      if (!token) throw new UnauthorizedError("/faelle/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await submitFaelleAttempt(token, itemId, answer, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleFaelleGiveUp = useCallback(
    async (itemId: string): Promise<CaseVerdict> => {
      if (!token) throw new UnauthorizedError("/faelle/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await giveUpFaelle(token, itemId, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleSatzbauAttempt = useCallback(
    async (itemId: string, order: string[]): Promise<ClauseVerdict> => {
      if (!token) throw new UnauthorizedError("/satzbau/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await submitSatzbauAttempt(token, itemId, order, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleSatzbauGiveUp = useCallback(
    async (itemId: string): Promise<ClauseVerdict> => {
      if (!token) throw new UnauthorizedError("/satzbau/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await giveUpSatzbau(token, itemId, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleZeitfaerbungAttempt = useCallback(
    async (itemId: string, answer: string): Promise<ZeitVerdict> => {
      if (!token) throw new UnauthorizedError("/zeitfaerbung/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await submitZeitfaerbungAttempt(token, itemId, answer, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleZeitfaerbungGiveUp = useCallback(
    async (itemId: string): Promise<ZeitVerdict> => {
      if (!token) throw new UnauthorizedError("/zeitfaerbung/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await giveUpZeitfaerbung(token, itemId, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleSprechenAttempt = useCallback(
    async (taskId: string, audio: Blob): Promise<SprechenVerdict> => {
      if (!token) throw new UnauthorizedError("/sprechen/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await submitSprechenAttempt(token, taskId, audio, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.passed);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleSprechenGiveUp = useCallback(
    async (taskId: string): Promise<SprechenVerdict> => {
      if (!token) throw new UnauthorizedError("/sprechen/give-up");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await giveUpSprechen(token, taskId, sid());
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGraded(dealKeyAtCall, res.passed);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  const handleSzenarioAttempt = useCallback(
    async (
      scenarioId: string,
      question: string,
      audio: Blob,
    ): Promise<StructureResult> => {
      if (!token) throw new UnauthorizedError("/szenario/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await submitSzenarioAttempt(
          token,
          scenarioId,
          question,
          audio,
          sid(),
        );
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          // Mirrors SzenarioTrainer's own `next()`: overcomplicated or a
          // give-up is a miss, everything else counts.
          recordGraded(
            dealKeyAtCall,
            res.verdict !== "overcomplicated" && !res.gaveUp,
          );
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGraded],
  );

  // szgiveup: szenario now has a real backend /give-up route
  // (szenario/routes.py::give_up, mirroring sprechen's), so this hits the
  // network like the other six Flow give-ups. It keeps one difference from
  // those siblings on purpose: a give-up must never dead-end the trainer, so
  // ANY failure here (network, 401, 5xx — session expiry included; there's
  // nothing left to authorize a retry against once the learner has already
  // conceded) falls back to the same client-side "gave up" shape this
  // handler always returned, logged once rather than surfaced as an error.
  // `recordGraded(dealKeyAtCall, false)` stays unconditional and in the same
  // spot — always a miss regardless of whether the network call lands.
  const handleSzenarioGiveUp = useCallback(
    async (scenarioId: string): Promise<StructureResult> => {
      const dealKeyAtCall = currentDealKeyRef.current;
      if (dealKeyAtCall !== null) {
        // Always a miss — `overcomplicated` + `gaveUp: true` is exactly what
        // SzenarioTrainer's own `next()` reads as false.
        recordGraded(dealKeyAtCall, false);
      }
      const fallback: StructureResult = {
        transcript: "",
        verdict: "overcomplicated",
        levelRead: "",
        coachMessage: "You gave up — no recording was judged.",
        sentences: [],
        skeleton: { kern: "", punkte: [], absprung: "", vokabelAnker: [] },
        gaveUp: true,
      };
      if (!token) return fallback;
      try {
        return await giveUpSzenario(token, scenarioId, sid());
      } catch (e) {
        console.warn("Szenario give-up route failed, using client-side fallback", e);
        return fallback;
      }
    },
    [token, sid, recordGraded],
  );

  const handleGenusArticle = useCallback(
    async (
      itemId: string,
      article: GenusArticle,
      // DATA-009: true for every drag after the first on this item — the
      // backend scores gender once per noun and ignores retries.
      retry: boolean = false,
    ): Promise<GenusArticleVerdict> => {
      if (!token) throw new UnauthorizedError("/genus/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await submitGenusArticle(token, itemId, article, sid(), retry);
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGenusGraded(dealKeyAtCall, res.correct);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGenusGraded],
  );

  const handleGenusGiveUp = useCallback(
    async (itemId: string): Promise<GenusArticleVerdict> => {
      if (!token) throw new UnauthorizedError("/genus/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await giveUpGenusArticle(token, itemId, sid());
        // Mirrors GenusTrainer's own `giveUp()`: `firstSlip()` runs
        // unconditionally, regardless of whatever `res.correct` says.
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          recordGenusGraded(dealKeyAtCall, false);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, sid, recordGenusGraded],
  );

  const handleVerbformenAttempt = useCallback(
    async (
      cardId: string,
      audio: Blob,
      sessionId: string,
    ): Promise<AttemptResult> => {
      if (!token) throw new UnauthorizedError("/verbformen/attempts");
      const dealKeyAtCall = currentDealKeyRef.current;
      try {
        const res = await submitVerbformenAttempt(token, cardId, audio, sessionId);
        if (dealKeyAtCall !== null && currentDealKeyRef.current === dealKeyAtCall) {
          // Mirrors VocabTrainer's own `handleNext()`: `wordOk` decides the
          // rep, unless a reveal in between overrides it (see
          // handleVerbformenReveal below, which records false immediately —
          // this attempt can still supersede it, matching "Try again"
          // genuinely resetting the trainer's local "revealed" flag).
          recordGraded(dealKeyAtCall, res.wordOk === true);
        }
        return res;
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession, recordGraded],
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

  const handleVerbformenReveal = useCallback(
    (cardId: string) => {
      if (!token) return;
      // Mirrors VocabTrainer's own `revealed` flag: set synchronously (the
      // trainer flips its local state the same way, before the network call
      // even resolves) so Skip sees this deal as graded-a-miss immediately.
      // A later "Try again" attempt can still supersede it — see
      // handleVerbformenAttempt above.
      const dealKeyAtCall = currentDealKeyRef.current;
      if (dealKeyAtCall !== null) {
        recordGraded(dealKeyAtCall, false);
      }
      revealVerbformenCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) expireSession();
      });
    },
    [token, expireSession, recordGraded],
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
                    {/* FLOW-007 review fix (SHOULD-FIX 3): the accuracy
                        figure is correct/graded, not correct/total — total
                        includes skips, which were never graded. */}
                    {totals.correct} / {totals.total - totals.skipped} ✓ ·{" "}
                    {totals.total} / {roundTarget}
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
                    <div className="mb-3 flex items-center justify-between">
                      <p className="font-body text-[11px] font-black uppercase tracking-[0.24em] text-flag-red">
                        {KICKER[deal.kind]}
                        {/* FLOW-007: the six typed drills' own "· second
                            try" item-header text is gated behind `!flow` and
                            never renders inside Flow — this is Flow's own
                            equivalent, inline in the same house style. */}
                        {isRetryDeal(deal) && (
                          <span className="normal-case tracking-normal text-ink-muted">
                            {" "}
                            · Noch einmal
                          </span>
                        )}
                      </p>
                      {/* FLOW-007 (Luis's addition): always available, never
                          disabled by a trainer's own busy/arming state — it
                          lives here, outside the trainer, on purpose.
                          Review fix (BLOCKER 1): once the mounted deal is
                          already graded (Check/Give-up ran, the trainer is
                          just showing its own verdict + its own Next),
                          relabel this "Next →" — pressing it now runs the
                          graded accounting, never a free skip. */}
                      <button
                        type="button"
                        onClick={handleSkip}
                        className="font-body text-[11px] font-semibold text-ink-muted underline underline-offset-2 hover:text-ink-soft"
                      >
                        {dealGraded ? "Next →" : "Skip"}
                      </button>
                    </div>

                    {deal.kind === "bauteil" && (
                      <BauteilTrainer
                        key={deal.key}
                        round={[deal.item]}
                        onAttempt={handleBauteilAttempt}
                        onNewRound={noopNewRound}
                        flow
                        onFlowDone={(correct) =>
                          handleItemDone(
                            "bauteil",
                            correct,
                            deal.key,
                            deal.item,
                          )
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
                          handleItemDone(
                            "verbindungen",
                            correct,
                            deal.key,
                            deal.item,
                          )
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
                          handleItemDone(
                            "zeitfaerbung",
                            correct,
                            deal.key,
                            deal.item,
                          )
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
                          handleItemDone("sprechen", correct, deal.key)
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
                          handleItemDone("szenario", correct, deal.key)
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
                          handleItemDone(
                            "genus",
                            correct,
                            deal.key,
                            deal.item,
                          )
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
                          handleItemDone(
                            "faelle",
                            correct,
                            deal.key,
                            deal.item,
                          )
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
                          handleItemDone(
                            "satzbau",
                            correct,
                            deal.key,
                            deal.item,
                          )
                        }
                        allowGiveUp
                        onGiveUp={handleSatzbauGiveUp}
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
                          handleItemDone("verbformen", correct, deal.key)
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
  totals: { total: number; correct: number; skipped: number };
  perExercise: Record<SourceKind, Tally>;
}) {
  const rows = ALL_KINDS.filter((k) => perExercise[k].done > 0);
  // FLOW-007 review fix (SHOULD-FIX 3): the headline is correct/graded, not
  // correct/total — total includes skips, which were never graded.
  const graded = totals.total - totals.skipped;
  return (
    <div
      className="rounded-[28px] border-[3px] border-line bg-card p-7 text-center"
      style={inkShadow}
    >
      <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
        Flow so far
      </p>
      <h2 className="mt-2 font-display text-[clamp(28px,5vw,40px)] font-black tracking-tight text-ink">
        {totals.correct} / {graded}
      </h2>
      {/* FLOW-007: only shown when the round actually had a skip — the
          ending screen here is English, so "übersprungen" becomes "skipped"
          to match the surrounding copy. */}
      {totals.skipped > 0 && (
        <p className="mt-1 font-body text-[13px] font-semibold text-ink-muted">
          {totals.skipped} skipped
        </p>
      )}
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
