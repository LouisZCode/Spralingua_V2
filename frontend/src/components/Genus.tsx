"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import GenusTrainer from "./genus/GenusTrainer";
import {
  fetchRound,
  submitArticle,
  submitPhrase,
  type Article,
  type ArticleVerdict,
  type GenusItem,
  type PhraseVerdict,
} from "./genus/api";
import { UnauthorizedError } from "./satzschmiede/api";

// Artikel-Anker — noun gender trained as a decision: drag der/die/das onto
// the word (the ending keeps the color — the anchor), then produce the
// nominative carrier phrase so the gender immediately does grammatical work.
// Auth-guarded page shell + round state (mirrors Zeitfaerbung.tsx); the
// trainer remounts per round.
export default function Genus() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [round, setRound] = useState<GenusItem[] | null>(null); // null = loading
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

  const sessionId = useCallback(() => {
    practiceSessionRef.current ??=
      "gn-" + crypto.randomUUID().replace(/-/g, "");
    return practiceSessionRef.current;
  }, []);

  const handleArticle = useCallback(
    async (itemId: string, article: Article): Promise<ArticleVerdict> => {
      if (!token) throw new UnauthorizedError("/genus/attempts");
      try {
        return await submitArticle(token, itemId, article, sessionId());
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut, sessionId]
  );

  const handlePhrase = useCallback(
    async (itemId: string, answer: string): Promise<PhraseVerdict> => {
      if (!token) throw new UnauthorizedError("/genus/attempts");
      try {
        return await submitPhrase(token, itemId, answer, sessionId());
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut, sessionId]
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
            Artikel-Anker
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            der · die · das
          </p>
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            Couldn&apos;t load a round — is the backend running?
          </p>
        ) : round === null ? null : (
          <GenusTrainer
            key={roundKey}
            round={round}
            onArticle={handleArticle}
            onPhrase={handlePhrase}
            onNewRound={loadRound}
          />
        )}
      </main>
    </div>
  );
}
