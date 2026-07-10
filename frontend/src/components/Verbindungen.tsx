"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import VerbindungenTrainer from "./verbindungen/VerbindungenTrainer";
import {
  fetchRound,
  submitAttempt,
  type ChunkItem,
  type ChunkVerdict,
} from "./verbindungen/api";
import { UnauthorizedError } from "./satzschmiede/api";

// Feste Verbindungen — GRAM-002 Exercise D: complete the fixed verb chunk
// (reflexive pronoun? which preposition? which case?) with reflexive and
// non-reflexive verbs mixed so nothing is predictable. Auth-guarded page
// shell + round state (mirrors Bauteil.tsx); the trainer remounts per round.
export default function Verbindungen() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [round, setRound] = useState<ChunkItem[] | null>(null); // null = loading
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
          signOut();
        } else {
          setError(true);
        }
      });
  }, [token, signOut]);

  useEffect(() => {
    loadRound();
  }, [loadRound]);

  const handleAttempt = useCallback(
    async (itemId: string, answer: string): Promise<ChunkVerdict> => {
      if (!token) throw new UnauthorizedError("/verbindungen/attempts");
      practiceSessionRef.current ??=
        "fv-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await submitAttempt(
          token,
          itemId,
          answer,
          practiceSessionRef.current
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
            Feste Verbindungen
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            pronoun? preposition? case?
          </p>
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            Couldn&apos;t load a round — is the backend running?
          </p>
        ) : round === null ? null : (
          <VerbindungenTrainer
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
