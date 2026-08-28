"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import SprechenTrainer from "./sprechen/SprechenTrainer";
import { loadError } from "./shared/copy";
import {
  fetchNudge,
  fetchRound,
  submitAttempt,
  type NudgeWord,
  type SpokenTask,
  type SprechenVerdict,
} from "./sprechen/api";
import {
  addWord,
  fetchGloss,
  UnauthorizedError,
  type GlossInfo,
} from "./satzschmiede/api";

// VARY-001: task ids already served this pool cycle, kept in localStorage so
// variety persists across page visits. Guarded the same way as the rest of
// the codebase (AuthContext.tsx, SetupView.tsx): try/catch, only ever
// touched outside the render path (inside loadRound, itself only called
// from an effect or a click handler), never during render.
const SEEN_STORAGE_KEY = "sprechen-seen-v1";

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

// Sprechen & transkribieren — GRAM-002 Exercise B: a constrained speaking
// task traps one verb-position structure; the learner speaks; the raw
// transcript is judged. This component is the auth-guarded page shell +
// round state (mirrors Bauteil.tsx); SprechenTrainer owns the interaction
// and remounts per round via `key`.
export default function Sprechen() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [round, setRound] = useState<SpokenTask[] | null>(null); // null = loading
  const [roundKey, setRoundKey] = useState(0); // remounts the trainer per round
  const [error, setError] = useState(false);

  // One Langfuse Session per practice sitting (OBS-007): minted lazily on the
  // first attempt, held for the whole page visit — "New round" top-ups and
  // retries share it because the ref lives above the per-round remounts.
  const practiceSessionRef = useRef<string | null>(null);
  // IDIOM-002: GermanWay's rephrase call needs the id at RENDER time, not
  // just inside handleAttempt's closure — a ref read directly in JSX would
  // stay stale (assigning `.current` doesn't trigger this component to
  // re-render), so `sid()` mints eagerly in place the first time anything
  // calls it, same convention as Flow.tsx's own `sid`.
  const sid = useCallback((): string => {
    practiceSessionRef.current ??=
      "spr-" + crypto.randomUUID().replace(/-/g, "");
    return practiceSessionRef.current;
  }, []);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  const loadRound = useCallback(() => {
    if (!token) return;
    setRound(null);
    const seen = readSeen();
    fetchRound(token, seen)
      .then(({ tasks, cycleReset }) => {
        setRound(tasks);
        setRoundKey((k) => k + 1);
        const served = tasks.map((t) => t.id);
        // VARY-001: an old backend omits cycleReset — treat it as "no reset"
        // (append), same as the default when the field is present but false.
        writeSeen(cycleReset ? served : [...seen, ...served]);
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
    async (taskId: string, audio: Blob): Promise<SprechenVerdict> => {
      if (!token) throw new UnauthorizedError("/sprechen/attempts");
      practiceSessionRef.current ??=
        "spr-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await submitAttempt(
          token,
          taskId,
          audio,
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

  // The vocab nudge is decorative — any failure resolves to "no pill", the
  // drill never waits on it or surfaces its errors (401 still signs out).
  const handleNudge = useCallback(
    async (taskId: string): Promise<NudgeWord[]> => {
      if (!token) return [];
      practiceSessionRef.current ??=
        "spr-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await fetchNudge(token, taskId, practiceSessionRef.current);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        return [];
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
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <header className="sticky top-0 z-50 border-b-[3px] border-line bg-card/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-2.5">
            <Image
              src="/mascot/raven.png"
              alt="Spralingua raven mascot"
              width={40}
              height={40}
              priority
              className="mascot-keyline h-9 w-9 select-none"
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
            Sprechen
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            constrained speaking
          </p>
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            {loadError("a round")}
          </p>
        ) : round === null ? null : (
          <SprechenTrainer
            key={roundKey}
            round={round}
            onAttempt={handleAttempt}
            onNewRound={loadRound}
            onGloss={handleGloss}
            onAdd={handleAddWord}
            onNudge={handleNudge}
            sessionId={sid()}
          />
        )}
      </main>
    </div>
  );
}
