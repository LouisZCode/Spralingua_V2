"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import ZeitfaerbungTrainer from "./zeitfaerbung/ZeitfaerbungTrainer";
import {
  fetchRound,
  submitAttempt,
  type ZeitItem,
  type ZeitVerdict,
} from "./zeitfaerbung/api";
import { UnauthorizedError } from "./satzschmiede/api";
import { loadError } from "./shared/copy";
import AppHeader from "@/components/shared/AppHeader";

// Zeitfaerbung — war / wurde / blieb: German splits "was" into a state, a
// becoming, and a staying (plus wurde's second job as the passive auxiliary).
// The gap never signals which one it wants; the learner has to read the
// meaning. Auth-guarded page shell + round state (mirrors Verbindungen.tsx);
// the trainer remounts per round.
export default function Zeitfaerbung() {
  const { token, ready, expireSession } = useAuth();
  const router = useRouter();

  const [round, setRound] = useState<ZeitItem[] | null>(null); // null = loading
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
    async (itemId: string, answer: string): Promise<ZeitVerdict> => {
      if (!token) throw new UnauthorizedError("/zeitfaerbung/attempts");
      practiceSessionRef.current ??=
        "zf-" + crypto.randomUUID().replace(/-/g, "");
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
            Zeitfärbung
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            war · wurde · blieb
          </p>
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            {loadError("a round")}
          </p>
        ) : round === null ? null : (
          <ZeitfaerbungTrainer
            key={roundKey}
            round={round}
            onAttempt={handleAttempt}
            onNewRound={loadRound}
          />
        )}
      </main>
    </div>
  );
}
