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

// SZEN-007: the question tier is no longer a client toggle (SZEN-005's
// localStorage-backed B1/B2 switch is gone) — it's read server-side from
// the learner's account level (`users.level`, LEVEL-001) and echoed back as
// `Scenario.tier`. The seen-token storage stays per-tier (a token's
// question index only makes sense against the list it was drawn from), just
// keyed by that tier instead of a manual toggle. The b1/b2 keys are the
// same ones SZEN-005 already used, kept for continuity with existing
// localStorage; a1/a2 are new.
type Tier = "a1" | "a2" | "b1" | "b2";

// LEVEL-001/LEVEL-002 bucket -> the tier this trainer requests before the
// server confirms — same mapping szenario/routes.py applies for real. B2+
// maps to "b2" (not a "b1"-shaped fallback); no level set (or AuthContext
// hasn't hydrated yet) guesses the base "b1" tier, same as the backend's own
// no-level fallback.
const BUCKET_TO_TIER: Record<string, Tier> = {
  A1: "a1",
  A2: "a2",
  B1: "b1",
  "B2+": "b2",
};

function expectedTier(level: string | null | undefined): Tier {
  return (level && BUCKET_TO_TIER[level]) || "b1";
}

function seenKey(tier: Tier): string {
  switch (tier) {
    case "a1":
      return "szenario-seen-a1-v1";
    case "a2":
      return "szenario-seen-a2-v1";
    case "b2":
      return "szenario-seen-b2-v1";
    default:
      return SEEN_STORAGE_KEY; // "b1" — the original, pre-tier key.
  }
}

function readSeen(tier: Tier): string[] {
  try {
    const raw = localStorage.getItem(seenKey(tier));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((x): x is string => typeof x === "string")
      : [];
  } catch {
    return [];
  }
}

function writeSeen(tier: Tier, list: string[]): void {
  try {
    localStorage.setItem(seenKey(tier), JSON.stringify(list));
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
  const { token, ready, user, signOut } = useAuth();
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
    // SZEN-007: guess the tier from the account level before asking — the
    // server needs a seen-list up front, so the client can't wait for the
    // response to know which one to send. The server's own guess (from the
    // SAME bucket->tier mapping, applied to `users.level`) will normally
    // agree; when it doesn't (stale/absent AuthContext read), the response's
    // `tier` is authoritative and storage below re-keys against it.
    const expected = expectedTier(user?.level);
    const seen = readSeen(expected);
    fetchScenario(token, seen)
      .then((s) => {
        setScenario(s);
        setScenarioKey((k) => k + 1);
        // VARY-001: an old backend omits questionIndex — skip the
        // localStorage update rather than writing a malformed token.
        if (typeof s.questionIndex === "number") {
          const servedTier = s.tier ?? expected;
          const priorSeen = servedTier === expected ? seen : readSeen(servedTier);
          const served = `${s.scenarioId}:${s.questionIndex}`;
          writeSeen(
            servedTier,
            s.cycleReset ? [served] : [...priorSeen, served]
          );
        }
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          signOut();
        } else {
          setError(true);
        }
      });
  }, [token, signOut, user?.level]);

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
          {/* SZEN-007: the tier is read from the account level server-side
              now — this pill is a read-only affordance, not a control, so
              the level setting stays visible without letting the learner
              fake it here. Same pill styling family as the removed
              SZEN-005 toggle. */}
          <div
            className="mt-3 inline-flex items-center gap-1.5 rounded-full border-[3px] border-ink bg-white px-4 py-1.5"
            title="Set your level on the practice menu"
          >
            <span className="font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink">
              {user?.level ?? "B1"}
            </span>
            <span className="font-body text-[9px] font-semibold uppercase tracking-[0.14em] text-ink-muted">
              level
            </span>
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
