"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import BriefTrainer from "./briefkasten/BriefTrainer";
import {
  fetchLetter,
  submitAttempt,
  UnauthorizedError,
  type AttemptResult,
  type Letter,
} from "./briefkasten/api";
import { addWord, fetchGloss, type GlossInfo } from "./satzschmiede/api";
import { InsufficientCoinsError } from "@/lib/coins";
import { OutOfCoinsPanel, refreshCoins } from "./shared/Coins";

// VARY-001: seed ids already served this pool cycle, kept in localStorage so
// variety persists across page visits. Guarded exactly like Szenario.tsx:
// try/catch, only ever touched outside the render path, never during render.
const SEEN_STORAGE_KEY = "briefkasten-seen-v1";

function readSeen(): string[] {
  try {
    const raw = localStorage.getItem(SEEN_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((x): x is string => typeof x === "string")
      : [];
  } catch {
    return [];
  }
}

function writeSeen(list: string[]): void {
  try {
    localStorage.setItem(SEEN_STORAGE_KEY, JSON.stringify(list));
  } catch {
    // Storage blocked/unavailable — variety just resets next visit.
  }
}

// Briefkasten — a letter arrives from someone; the learner writes back in
// German across two graded attempts (hints first, corrections second). This
// component is the auth-guarded page shell + letter state (mirrors
// Szenario.tsx); BriefTrainer owns the interaction and remounts per letter
// via `key`.
export default function Briefkasten() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [letter, setLetter] = useState<Letter | null>(null); // null = loading
  const [letterKey, setLetterKey] = useState(0); // remounts the trainer per letter
  const [error, setError] = useState(false);
  // PAY-002: Briefkasten charges the LETTER (GET /briefkasten/letter, 15
  // coins for the whole cycle) — the two attempts that follow ride free on
  // that ticket. So the 402 arrives here, on the mint, not inside
  // BriefTrainer's submit path, and this is the component that has to own the
  // out-of-coins state.
  const [insufficient, setInsufficient] = useState<{
    needed: number;
    available: number;
  } | null>(null);

  // One Langfuse Session per practice sitting (OBS-007): minted lazily on the
  // first attempt, held for the whole page visit — "New letter" top-ups
  // share it because the ref lives above the per-letter remounts.
  const practiceSessionRef = useRef<string | null>(null);
  // IDIOM-002: the letter GET fires before any attempt (loadLetter runs on
  // mount, ahead of handleAttempt), so it needs the id minted eagerly rather
  // than waiting on the lazy `??=` inside handleAttempt below. `sid()` mints
  // in place the first time anything calls it — same convention as Flow.tsx.
  const sid = useCallback((): string => {
    practiceSessionRef.current ??=
      "brf-" + crypto.randomUUID().replace(/-/g, "");
    return practiceSessionRef.current;
  }, []);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  const loadLetter = useCallback(() => {
    if (!token) return;
    setLetter(null);
    setError(false);
    setInsufficient(null);
    const seen = readSeen();
    fetchLetter(token, seen, undefined, sid())
      .then((l) => {
        setLetter(l);
        setLetterKey((k) => k + 1);
        writeSeen(l.cycleReset ? [l.seedId] : [...seen, l.seedId]);
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          signOut();
        } else if (e instanceof InsufficientCoinsError) {
          setInsufficient({ needed: e.needed, available: e.available });
          refreshCoins();
        } else {
          setError(true);
        }
      });
  }, [token, signOut, sid]);

  useEffect(() => {
    loadLetter();
  }, [loadLetter]);

  const handleAttempt = useCallback(
    async (params: {
      seedId: string;
      letterBody: string;
      points: string[];
      response: string;
      firstAttempt?: string;
      attempt: 1 | 2;
    }): Promise<AttemptResult> => {
      if (!token) throw new UnauthorizedError("/briefkasten/attempts");
      practiceSessionRef.current ??=
        "brf-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await submitAttempt(token, {
          ...params,
          sessionId: practiceSessionRef.current,
        });
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut]
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
          signOut();
        }
        throw e;
      }
    },
    [token, signOut]
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

      <main className="relative mx-auto flex w-full max-w-4xl flex-1 flex-col justify-center px-6 py-12">
        <div className="mb-5 text-center">
          <h1 className="font-display text-[24px] font-black tracking-tight text-ink">
            Briefkasten
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            letter writing
          </p>
        </div>

        {insufficient ? (
          <OutOfCoinsPanel
            needed={insufficient.needed}
            available={insufficient.available}
          />
        ) : error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            Couldn&apos;t load a letter — is the backend running?
          </p>
        ) : letter === null ? null : (
          <BriefTrainer
            key={letterKey}
            letter={letter}
            onAttempt={handleAttempt}
            onNewLetter={loadLetter}
            onGloss={handleGloss}
            onAdd={handleAddWord}
          />
        )}
      </main>
    </div>
  );
}
