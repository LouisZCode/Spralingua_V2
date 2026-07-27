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
  type RoundItem as BauteilItem,
  type BauteilVerdict,
} from "./bauteil/api";

import VerbindungenTrainer from "./verbindungen/VerbindungenTrainer";
import {
  fetchRound as fetchVerbindungenRound,
  submitAttempt as submitVerbindungenAttempt,
  type ChunkItem,
  type ChunkVerdict,
} from "./verbindungen/api";

import ZeitfaerbungTrainer from "./zeitfaerbung/ZeitfaerbungTrainer";
import {
  fetchRound as fetchZeitfaerbungRound,
  submitAttempt as submitZeitfaerbungAttempt,
  type ZeitItem,
  type ZeitVerdict,
} from "./zeitfaerbung/api";

import GenusTrainer from "./genus/GenusTrainer";
import {
  fetchRound as fetchGenusRound,
  fetchMeta as fetchGenusMeta,
  submitArticle as submitGenusArticle,
  submitPhrase as submitGenusPhrase,
  type Article as GenusArticle,
  type ArticleVerdict as GenusArticleVerdict,
  type GenusItem,
  type PhraseVerdict as GenusPhraseVerdict,
} from "./genus/api";

import SprechenTrainer from "./sprechen/SprechenTrainer";
import {
  fetchRound as fetchSprechenRound,
  submitAttempt as submitSprechenAttempt,
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
type Deal =
  | { kind: "bauteil"; key: number; item: BauteilItem }
  | { kind: "verbindungen"; key: number; item: ChunkItem }
  | { kind: "zeitfaerbung"; key: number; item: ZeitItem }
  | { kind: "sprechen"; key: number; item: SpokenTask }
  | { kind: "genus"; key: number; item: GenusItem }
  | { kind: "satz"; key: number; card: DeckCard }
  | { kind: "verbformen"; key: number; card: DeckCard };

function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// Card sources (satz/verbformen) deal one card per turn from an endless
// cycle: due, then new, then later, shuffled within each tier; once the
// order empties it's rebuilt from the current (possibly shrunk-by-removal)
// deck, so the cycle never runs dry.
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
  satz: CardCycle;
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
    satz: { deck: [], order: [] },
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
  if (kind === "satz") return bag.satz.deck.length;
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
    const card = nextCard(bag.satz);
    return card ? { kind, key, card } : null;
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

// FLOW-001: endless mixed-practice mode — one item at a time, drawn randomly
// across the seven existing exercises, running until the learner hits Finish.
// This component is the auth-guarded page shell + the dealing/tally state;
// each existing trainer runs unmodified except for its opt-in `flow` prop.
export default function Flow() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [phase, setPhase] = useState<"loading" | "error" | "ready">("loading");
  const [deal, setDeal] = useState<Deal | null>(null);
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

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  const dealNext = useCallback(
    (prevKind: SourceKind | null) => {
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
      setDeal(next);
    },
    [token]
  );

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
        bag.satz = { deck: payload.cards, order: orderCycle(payload.cards) };
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
      setPhase("ready");
      dealNext(null);
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
      dealNext(kind);
    },
    [dealNext]
  );

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
        return await submitSatzAttempt(token, cardId, audio, sessionId);
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
      // cycle so future deals never pick it again.
      const bag = bagRef.current;
      bag.satz = {
        deck: bag.satz.deck.filter((c) => c.id !== cardId),
        order: bag.satz.order.filter((id) => id !== cardId),
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
        <div className="mb-5 text-center">
          <h1 className="font-display text-[24px] font-black tracking-tight text-ink">
            Flow
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            alle Übungen · endlos
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

            {deal !== null && (
              <>
                <p className="mb-3 text-center font-body text-[11px] font-black uppercase tracking-[0.24em] text-flag-red">
                  {KICKER[deal.kind]}
                </p>

                {deal.kind === "bauteil" && (
                  <BauteilTrainer
                    key={deal.key}
                    round={[deal.item]}
                    onAttempt={handleBauteilAttempt}
                    onNewRound={noopNewRound}
                    flow
                    onFlowDone={(correct) => handleItemDone("bauteil", correct)}
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
