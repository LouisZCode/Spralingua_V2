"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import VocabTrainer from "./satzschmiede/VocabTrainer";
import {
  fetchDeck,
  removeCard,
  revealCard,
  submitAttempt,
  UnauthorizedError,
  type AttemptResult,
} from "./verbformen/api";
import { explainAttempt, flagVerdict } from "./satzschmiede/api";
import { loadError } from "./shared/copy";
import type { DeckCard } from "./satzschmiede/deck";
import AppHeader from "@/components/shared/AppHeader";

const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;

// Verbformen — GRAM-002 Exercise C: the verb principal-parts drill. The
// user's own finding: past-tense misses are LEXICON, not rule ("every miss
// was a strong verb wearing a weak ending"), so the prescription is
// flashcarding — and Satzschmiede's spoken-past sibling cards ARE those
// flashcards. The deck AUTO-FEEDS from the Satzschmiede pool (every verb
// added there brings its past sibling here), but the drill's state is its
// own: /verbformen/* writes the user_verbformen overlay, so schedules,
// reveals and removals in this mode never touch Satzschmiede. Same trainer,
// same examiner (Perfekt OR Präteritum both pass); the OBS-007 session
// prefix ("vf-") groups Verbformen sittings apart in Langfuse.
export default function Verbformen() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [deck, setDeck] = useState<DeckCard[] | null>(null); // null = loading
  const [error, setError] = useState(false);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  const refreshDeck = useCallback(() => {
    if (!token) return;
    fetchDeck(token)
      .then(setDeck)
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          signOut();
        } else {
          setError(true);
        }
      });
  }, [token, signOut]);

  useEffect(() => {
    refreshDeck();
  }, [refreshDeck]);

  const handleRemove = useCallback(
    async (cardId: string) => {
      if (!token) return;
      try {
        await removeCard(token, cardId);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
          return;
        }
      }
      refreshDeck();
    },
    [token, signOut, refreshDeck]
  );

  const handleReveal = useCallback(
    (cardId: string) => {
      if (!token) return;
      revealCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) signOut();
      });
    },
    [token, signOut]
  );

  const handleAttempt = useCallback(
    async (
      cardId: string,
      audio: Blob,
      sessionId: string
    ): Promise<AttemptResult> => {
      if (!token) throw new UnauthorizedError("/verbformen/attempts");
      try {
        return await submitAttempt(token, cardId, audio, sessionId);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut]
  );

  // SATZ-007: unpack a correction on demand — same /satz/explain call as
  // Satzschmiede (keyed on the catalog card, not pool ownership, so it works
  // against this drill's overlay deck too).
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
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut]
  );

  // SATZ-008: file a "this verdict seems wrong" flag on the judgement's own
  // Langfuse trace. Auth errors sign out here (same policy as every other
  // call); everything else rethrows so the trainer can fall back its button.
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
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut]
  );

  if (!ready || !token) {
    return null;
  }

  // The drill's material: every spoken-past sibling in the pool. Verbs arrive
  // as pairs (present + past) when added anywhere in Satzschmiede, so this
  // grows by itself as the learner's verb vocabulary grows. /verbformen/deck
  // already serves only the past siblings (minus drill-local removals).
  const verbDeck = deck;
  const empty = verbDeck !== null && verbDeck.length === 0;

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
        <div className="mb-5 text-center">
          <h1 className="font-display text-[24px] font-black tracking-tight text-ink">
            Verbformen
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            {verbDeck && verbDeck.length > 0
              ? `spoken past · ${verbDeck.length} verbs`
              : "spoken past"}
          </p>
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            {loadError("your verbs")}
          </p>
        ) : verbDeck === null ? null : empty ? (
          /* No verb pairs yet — every verb added in Satzschmiede brings its
             spoken-past sibling along, so the fix is to add verbs there. */
          <div className="text-center">
            <h2 className="font-display text-[clamp(26px,5vw,36px)] font-black leading-tight tracking-tight text-ink">
              No verbs in your pool yet.
            </h2>
            <p className="mx-auto mt-3 max-w-[380px] font-body text-[15px] leading-relaxed text-ink-soft">
              Verbformen drills the spoken past of your own verbs — ist
              gefahren, hat gedacht, war, wollte. Add a few verbs in
              Satzschmiede first; every verb brings its past form along.
            </p>
            <Link
              href="/satzschmiede"
              className="btn-3d mt-7 inline-flex items-center gap-2 rounded-[20px] border-[3px] border-red-line bg-flag-red-fill px-7 py-3.5 font-display text-[15px] font-black uppercase tracking-[0.16em] text-on-fill"
              style={redShadow}
            >
              Open Satzschmiede
            </Link>
          </div>
        ) : (
          <VocabTrainer
            deck={verbDeck}
            onRemove={handleRemove}
            onAttempt={handleAttempt}
            onReveal={handleReveal}
            onExplain={handleExplain}
            onFlag={handleFlag}
            sessionPrefix="vf"
          />
        )}
      </main>
    </div>
  );
}
