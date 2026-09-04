"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import FaelleTrainer from "./faelle/FaelleTrainer";
import { loadError } from "./shared/copy";
import {
  fetchRound,
  submitAttempt,
  type CaseItem,
  type CaseVerdict,
} from "./faelle/api";
import {
  addWord,
  fetchGloss,
  UnauthorizedError,
  type GlossInfo,
} from "./satzschmiede/api";
import AppHeader from "@/components/shared/AppHeader";

// Fälle — GRAM-006 Proposal-1 ("the case cluster"): six case-decision
// patterns (wechselpraepositionen, dativ-praepositionen,
// akkusativ-praepositionen, akkusativ-artikel, dativ-verben,
// pronomen-akk-dat) drilled interleaved so no single pattern lets the
// learner coast. Auth-guarded page shell + round state (mirrors
// Verbindungen.tsx); the trainer remounts per round.
export default function Faelle() {
  const { token, ready, expireSession } = useAuth();
  const router = useRouter();

  const [round, setRound] = useState<CaseItem[] | null>(null); // null = loading
  const [roundKey, setRoundKey] = useState(0); // remounts the trainer per round
  const [error, setError] = useState(false);

  // One Langfuse Session per practice sitting (OBS-007): minted lazily on the
  // first attempt, held for the whole page visit above the per-round remounts.
  const practiceSessionRef = useRef<string | null>(null);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  const loadRound = useCallback(() => {
    if (!token) return;
    setRound(null);
    fetchRound(token)
      .then((items) => {
        setRound(items);
        setRoundKey((k) => k + 1);
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          expireSession();
        } else {
          setError(true);
        }
      });
  }, [token, expireSession]);

  useEffect(() => {
    loadRound();
  }, [loadRound]);

  const handleAttempt = useCallback(
    async (itemId: string, answer: string): Promise<CaseVerdict> => {
      if (!token) throw new UnauthorizedError("/faelle/attempts");
      practiceSessionRef.current ??=
        "fa-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await submitAttempt(
          token,
          itemId,
          answer,
          practiceSessionRef.current
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          expireSession();
        }
        throw e;
      }
    },
    [token, expireSession]
  );

  // UI-007: word-gloss popover wiring — same auth-guarded pattern as
  // handleAttempt above. Both are optional on the trainer's props.
  const handleGloss = useCallback(
    async (word: string, context: string): Promise<GlossInfo> => {
      if (!token) throw new UnauthorizedError("/satz/gloss");
      try {
        return await fetchGloss(
          token,
          word,
          context,
          practiceSessionRef.current ?? undefined
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          expireSession();
        }
        throw e;
      }
    },
    [token, expireSession]
  );

  const handleAddWord = useCallback(
    async (lemma: string): Promise<{ glossRemaining?: number } | void> => {
      if (!token) throw new UnauthorizedError("/satz/cards");
      try {
        // SATZ-013: this is the gloss popover's one-tap add — mark it so
        // the backend counts it against the daily gloss-add cap.
        const res = await addWord(
          token,
          lemma,
          practiceSessionRef.current ?? undefined,
          "gloss"
        );
        return { glossRemaining: res.glossRemaining };
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          expireSession();
        }
        throw e;
      }
    },
    [token, expireSession]
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
        <div className="mb-5 text-center">
          <h1 className="font-display text-[24px] font-black tracking-tight text-ink">
            Fälle
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            der · dem · den
          </p>
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            {loadError("a round")}
          </p>
        ) : round === null ? null : (
          <FaelleTrainer
            key={roundKey}
            round={round}
            onAttempt={handleAttempt}
            onNewRound={loadRound}
            onGloss={handleGloss}
            onAdd={handleAddWord}
          />
        )}
      </main>
    </div>
  );
}
