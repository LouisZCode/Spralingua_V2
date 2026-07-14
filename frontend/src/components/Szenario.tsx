"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import SzenarioTrainer from "./szenario/SzenarioTrainer";
import {
  fetchScenario,
  submitAttempt,
  type Scenario,
  type StructureResult,
} from "./szenario/api";
import { UnauthorizedError } from "./satzschmiede/api";

// Szenario-Sparring — a persona shows one German question in a scene; the
// learner answers with one recording; the backend judges the answer's
// STRUCTURE (anchor / one-idea-per-sentence / clean close), never grammar.
// This component is the auth-guarded page shell + scenario state (mirrors
// Sprechen.tsx); SzenarioTrainer owns the interaction and remounts per
// question via `key`.
export default function Szenario() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [scenario, setScenario] = useState<Scenario | null>(null); // null = loading
  const [scenarioKey, setScenarioKey] = useState(0); // remounts the trainer per question
  const [error, setError] = useState(false);

  // Once the learner has clicked past the "How it works" card, later
  // remounts (New question) should land straight on "scene" — the intro is
  // a one-time thing, not per-question. A ref (not state) so flipping it
  // never itself triggers a render.
  const hasStartedRef = useRef(false);

  // One Langfuse Session per practice sitting (OBS-007): minted lazily on the
  // first attempt, held for the whole page visit — "New question" top-ups
  // share it because the ref lives above the per-question remounts.
  const practiceSessionRef = useRef<string | null>(null);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  const loadScenario = useCallback(() => {
    if (!token) return;
    setScenario(null);
    fetchScenario(token)
      .then((s) => {
        setScenario(s);
        setScenarioKey((k) => k + 1);
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
    loadScenario();
  }, [loadScenario]);

  const handleAttempt = useCallback(
    async (
      scenarioId: string,
      question: string,
      audio: Blob
    ): Promise<StructureResult> => {
      if (!token) throw new UnauthorizedError("/szenario/attempts");
      practiceSessionRef.current ??=
        "szn-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await submitAttempt(
          token,
          scenarioId,
          question,
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
            Szenario-Sparring
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            structure, not grammar
          </p>
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            Couldn&apos;t load a scenario — is the backend running?
          </p>
        ) : scenario === null ? null : (
          <SzenarioTrainer
            key={scenarioKey}
            scenario={scenario}
            initialPhase={hasStartedRef.current ? "scene" : "intro"}
            onStart={() => {
              hasStartedRef.current = true;
            }}
            onAttempt={handleAttempt}
            onNewQuestion={loadScenario}
          />
        )}
      </main>
    </div>
  );
}
