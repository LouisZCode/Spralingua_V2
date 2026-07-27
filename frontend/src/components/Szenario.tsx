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
import {
  addWord,
  fetchGloss,
  UnauthorizedError,
  type GlossInfo,
} from "./satzschmiede/api";

// VARY-001: "scenarioId:questionIndex" tokens already served this pool
// cycle, kept in localStorage so variety persists across page visits.
// Guarded the same way as the rest of the codebase (AuthContext.tsx,
// SetupView.tsx): try/catch, only ever touched outside the render path
// (inside loadScenario, itself only called from an effect or a click
// handler), never during render.
const SEEN_STORAGE_KEY = "szenario-seen-v1";

// SZEN-005: manual level toggle (B1 base tier / B2 harder tier), persisted
// like the seen-tokens. Per-tier seen lists so each tier cycles its own
// pool without cross-contaminating variety state. Migrates into a users
// table column when profile machinery lands.
const LEVEL_STORAGE_KEY = "szenario-level-v1";

function readLevel(): "b1" | "b2" {
  try {
    return localStorage.getItem(LEVEL_STORAGE_KEY) === "b2" ? "b2" : "b1";
  } catch {
    return "b1";
  }
}

function seenKey(level: "b1" | "b2"): string {
  return level === "b2" ? "szenario-seen-b2-v1" : SEEN_STORAGE_KEY;
}

function readSeen(level: "b1" | "b2"): string[] {
  try {
    const raw = localStorage.getItem(seenKey(level));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((x): x is string => typeof x === "string")
      : [];
  } catch {
    return [];
  }
}

function writeSeen(level: "b1" | "b2", list: string[]): void {
  try {
    localStorage.setItem(seenKey(level), JSON.stringify(list));
  } catch {
    // Storage blocked/unavailable — variety just resets next visit.
  }
}

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

  // SZEN-005: manual B1/B2 tier toggle. Default to "b1" and hydrate from
  // localStorage in an effect — same SSR-safe pattern as AuthContext.tsx's
  // `ready` flag and SetupView.tsx's dev-unlock hydration (reading storage
  // during render would mismatch server HTML). `levelReady` gates the first
  // fetch below so a stored "b2" can't leak an initial "b1" request: without
  // it, loadScenario's effect would fire once on mount with the default
  // "b1" (before this hydration effect's setLevel has committed) and again
  // right after with "b2", double-fetching.
  const [level, setLevel] = useState<"b1" | "b2">("b1");
  const [levelReady, setLevelReady] = useState(false);

  useEffect(() => {
    if (readLevel() === "b2") {
      setLevel("b2");
    }
    setLevelReady(true);
  }, []);

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
    if (!token || !levelReady) return;
    setScenario(null);
    const seen = readSeen(level);
    fetchScenario(token, seen, level)
      .then((s) => {
        setScenario(s);
        setScenarioKey((k) => k + 1);
        // VARY-001: an old backend omits questionIndex — skip the
        // localStorage update rather than writing a malformed token.
        if (typeof s.questionIndex === "number") {
          const served = `${s.scenarioId}:${s.questionIndex}`;
          writeSeen(level, s.cycleReset ? [served] : [...seen, served]);
        }
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          signOut();
        } else {
          setError(true);
        }
      });
  }, [token, signOut, level, levelReady]);

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

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-12">
        <div className="mb-5 text-center">
          <h1 className="font-display text-[24px] font-black tracking-tight text-ink">
            Szenario-Sparring
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            structure, not grammar
          </p>
          {/* SZEN-005: manual tier switch — B2 swaps every scene's questions
              for the harder set. Personal-level DB column comes later. */}
          <div className="mt-3 inline-flex overflow-hidden rounded-full border-[3px] border-ink">
            {(["b1", "b2"] as const).map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => {
                  if (l === level) return;
                  setLevel(l);
                  try {
                    localStorage.setItem(LEVEL_STORAGE_KEY, l);
                  } catch {}
                }}
                className={`px-4 py-1.5 font-display text-[12px] font-black uppercase tracking-[0.16em] transition-colors ${
                  l === level
                    ? "bg-ink text-white"
                    : "bg-white text-ink hover:text-flag-red"
                }`}
              >
                {l === "b1" ? "B1" : "B2"}
              </button>
            ))}
          </div>
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
            onGloss={handleGloss}
            onAdd={handleAddWord}
          />
        )}
      </main>
    </div>
  );
}
