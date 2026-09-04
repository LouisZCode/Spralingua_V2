"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import BauteilTrainer from "./bauteil/BauteilTrainer";
import {
  fetchRound,
  submitAttempt,
  type BauteilVerdict,
  type RoundItem,
} from "./bauteil/api";
import { UnauthorizedError } from "./satzschmiede/api";
import { loadError } from "./shared/copy";
import AppHeader from "@/components/shared/AppHeader";

// Bauteil-Sätze — GRAM-002 Exercise A: raw uninflected parts in, the
// correctly declined phrase out, typed. This component is the auth-guarded
// page shell + round state (mirrors Satzschmiede.tsx); BauteilTrainer owns
// the drill interaction and remounts per round via `key`.
export default function Bauteil() {
  const { token, ready, expireSession } = useAuth();
  const router = useRouter();

  const [round, setRound] = useState<RoundItem[] | null>(null); // null = loading
  const [roundKey, setRoundKey] = useState(0); // remounts the trainer per round
  const [error, setError] = useState(false);

  // One Langfuse Session per practice sitting (OBS-007): minted lazily on the
  // first attempt (browsing an unanswered round never creates a session), then
  // held for the whole page visit — "New round" top-ups share it, because the
  // ref lives HERE, above the per-round trainer remounts. Leaving the route
  // retires it.
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
          // Expired session JWT (AUTH-001, no refresh) — UI-016: this shows
          // the shared session-expiry modal instead of a silent sign-out,
          // so the round in progress stays in memory.
          expireSession();
        } else {
          setError(true);
        }
      });
  }, [token, expireSession]);

  useEffect(() => {
    loadRound();
  }, [loadRound]);

  // Judge one typed phrase. Auth errors show the session-expiry modal here (same policy as every
  // other call); everything else rethrows so the trainer can show a message.
  const handleAttempt = useCallback(
    async (itemId: string, answer: string): Promise<BauteilVerdict> => {
      if (!token) throw new UnauthorizedError("/bauteil/attempts");
      practiceSessionRef.current ??=
        "bau-" + crypto.randomUUID().replace(/-/g, "");
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
            Bauteil-Sätze
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            declension practice
          </p>
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            {loadError("a round")}
          </p>
        ) : round === null ? null : (
          <BauteilTrainer
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
