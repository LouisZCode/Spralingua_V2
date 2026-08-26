"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import VocabTrainer from "./satzschmiede/VocabTrainer";
import SatzschmiedePicker from "./satzschmiede/SatzschmiedePicker";
import PackModal from "./satzschmiede/PackModal";
import {
  addWord,
  explainAttempt,
  fetchDeck,
  fetchGloss,
  flagVerdict,
  genderMissCard,
  removeCard,
  revealCard,
  submitAttempt,
  UnauthorizedError,
  type AttemptResult,
  type GlossInfo,
} from "./satzschmiede/api";
import type { DeckCard } from "./satzschmiede/deck";
import { fetchMeta } from "./genus/api";
import { postModeComplete } from "./development/api";
import SoundToggle from "./shared/SoundToggle";
import ThemeToggle from "./shared/ThemeToggle";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;

// The review-queue cap VocabTrainer's buildQueue applies, most-overdue
// first — matches satz/routes.py's REVIEW_SHARE: reviews claim at most 15
// of the 20-card daily target, so the cap here and the allowance the server
// computed from it stay honest with each other (a bigger backlog waits for
// tomorrow instead of burying today).
const REVIEW_CAP = 15;

// Vocabulary trainer ("Satzschmiede"). The deck is the user's own pool served
// by GET /satz/deck. The trainer is the main surface; words and packs are
// added via the "Add Cards" popup (PackModal) — an empty pool shows a CTA
// into that popup instead of cards. This component is the auth-guarded page
// shell + state; VocabTrainer owns the card interaction.
export default function Satzschmiede() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [deck, setDeck] = useState<DeckCard[] | null>(null); // null = loading
  // Server-decided daily new-word drip — refreshed alongside `deck` on every
  // refetch (SATZ dosing P2: guaranteed >=5, up to 20, see satz/routes.py).
  const [newAllowance, setNewAllowance] = useState(0);
  const [error, setError] = useState(false);
  const [packsOpen, setPacksOpen] = useState(false); // the "Add Cards" popup
  // SATZ-012: true while VocabTrainer shows its post-round end screen.
  const [trainerDone, setTrainerDone] = useState(false);
  // Picker phase: "picker" | "playing"
  const [phase, setPhase] = useState<"picker" | "playing">("picker");
  // Chosen round target from picker
  const [roundTarget, setRoundTarget] = useState<number | null>(null);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  const refreshDeck = useCallback(() => {
    if (!token) return;
    fetchDeck(token)
      .then((payload) => {
        setDeck(payload.cards);
        setNewAllowance(payload.newAllowance);
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          // Expired session JWT — clear it; the guard above then routes to
          // the landing page for a fresh Google sign-in (AUTH-001, no refresh).
          signOut();
        } else {
          setError(true);
        }
      });
  }, [token, signOut]);

  // Load deck when picker completes
  useEffect(() => {
    if (phase === "playing") {
      refreshDeck();
    }
  }, [phase, refreshDeck]);

  const handlePickerStart = useCallback((count: number) => {
    setRoundTarget(count);
    setPhase("playing");
  }, []);

  const handleRemove = useCallback(
    async (cardId: string) => {
      if (!token) return;
      try {
        await removeCard(token, cardId);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
          return;
        }
        // Non-fatal (e.g. already gone): the refetch below re-syncs the UI.
      }
      refreshDeck();
    },
    [token, signOut, refreshDeck],
  );

  // Record a reveal-lapse. Fire-and-forget: the trainer's queue behaviour is
  // local — this write only keeps tomorrow's schedule honest, so a failure
  // isn't worth interrupting the flip for.
  const handleReveal = useCallback(
    (cardId: string) => {
      if (!token) return;
      revealCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) signOut();
      });
    },
    [token, signOut],
  );

  // SATZ-010: a wrong gender pick — same fire-and-forget lapse policy as a
  // reveal.
  const handleGenderMiss = useCallback(
    (cardId: string) => {
      if (!token) return;
      genderMissCard(token, cardId).catch((e) => {
        if (e instanceof UnauthorizedError) signOut();
      });
    },
    [token, signOut],
  );

  // SATZ-018: word-gloss popover wiring (UI-007/UI-009 machinery) for the
  // vocab card's example sentence and corrected-sentence fix — same
  // auth-guarded pattern as Sprechen.tsx/Verbindungen.tsx's handleGloss/
  // handleAddWord. VocabTrainer mints its own OBS-007 practice-session id
  // internally and doesn't expose it here, so gloss lookups get their own
  // separate session id instead (TandemChat.tsx's glossSessionRef
  // precedent) — it's only a Langfuse grouping label, not the graded
  // attempt itself, so a second id is harmless.
  const glossSessionRef = useRef<string | null>(null);

  const handleGloss = useCallback(
    async (word: string, context: string): Promise<GlossInfo> => {
      if (!token) throw new UnauthorizedError("/satz/gloss");
      glossSessionRef.current ??=
        "satz-gloss-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await fetchGloss(token, word, context, glossSessionRef.current);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut],
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
          glossSessionRef.current ?? undefined,
          "gloss",
        );
        return { glossRemaining: res.glossRemaining };
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut],
  );

  // SATZ-010 hint: Artikel-Anker's ending labels, fetched on first use.
  const handleGenderCues = useCallback(async () => {
    if (!token) throw new UnauthorizedError("/genus/rules");
    try {
      return (await fetchMeta(token)).endings;
    } catch (e) {
      if (e instanceof UnauthorizedError) signOut();
      throw e;
    }
  }, [token, signOut]);

  // Judge one recorded sentence. Auth errors sign out here (same policy as
  // every other call); everything else rethrows so the trainer can show a
  // learner-facing message next to the mic.
  const handleAttempt = useCallback(
    async (
      cardId: string,
      audio: Blob,
      sessionId: string,
    ): Promise<AttemptResult> => {
      if (!token) throw new UnauthorizedError("/satz/attempts");
      try {
        return await submitAttempt(token, cardId, audio, sessionId);
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut],
  );

  // SATZ-007: unpack a correction on demand. Auth errors sign out here (same
  // policy as every other call); everything else rethrows so the trainer can
  // show its own fallback message next to the button.
  const handleExplain = useCallback(
    async (
      cardId: string,
      transcript: string,
      corrected: string,
      error: string | null,
      sessionId?: string,
    ): Promise<string> => {
      if (!token) throw new UnauthorizedError("/satz/explain");
      try {
        return await explainAttempt(
          token,
          cardId,
          transcript,
          corrected,
          error,
          sessionId,
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut],
  );

  // SATZ-008: file a "this verdict seems wrong" flag on the judgement's own
  // Langfuse trace. Auth errors sign out here (same policy as every other
  // call); everything else rethrows so the trainer can fall back its button.
  const handleFlag = useCallback(
    async (
      traceId: string,
      cardId: string | null,
      transcript: string,
      verdict: string,
      sessionId?: string,
    ): Promise<void> => {
      if (!token) throw new UnauthorizedError("/satz/flag");
      try {
        await flagVerdict(
          token,
          traceId,
          cardId,
          transcript,
          verdict,
          sessionId,
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut],
  );

  // GAME-001: ping the streak the moment the post-round end screen shows —
  // trainerDone (SATZ-012) is exactly that signal. Guarded by a ref (not
  // just the trainerDone state) so the true→true re-render in between
  // doesn't refire; reset when trainerDone drops back to false so the next
  // round's end screen pings again.
  const satzModePingedRef = useRef(false);
  useEffect(() => {
    if (trainerDone && !satzModePingedRef.current) {
      satzModePingedRef.current = true;
      if (token) postModeComplete(token, "satz");
    } else if (!trainerDone) {
      satzModePingedRef.current = false;
    }
  }, [trainerDone, token]);

  if (!ready || !token) {
    return null;
  }

  const emptyPool = deck !== null && deck.length === 0;
  const label =
    deck === null || error
      ? " "
      : emptyPool
        ? "your pool"
        : `your pool · ${deck.length} words`;

  // Show picker first
  if (phase === "picker") {
    return (
      <div className="relative flex min-h-screen flex-col bg-paper text-ink">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
        />

        <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-card/85 backdrop-blur">
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
            <div className="flex items-center gap-4">
              <ThemeToggle />
              <SoundToggle />
              <Link
                href="/practice"
                className="font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
              >
                ← Menu
              </Link>
            </div>
          </div>
        </header>

        <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-12">
          <SatzschmiedePicker onStart={handlePickerStart} />
        </main>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-card/85 backdrop-blur">
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
          <div className="flex items-center gap-4">
            <ThemeToggle />
            <SoundToggle />
            <Link
              href="/practice"
              className="font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
            >
              ← Menu
            </Link>
          </div>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-12">
        {/* Page title — the module name reads as a heading; the pool count
            stays a quiet sub-line next to the Add Cards action. */}
        <div className="mb-5 text-center">
          <h1 className="font-display text-[24px] font-black tracking-tight text-ink">
            Satzschmiede
          </h1>
          {!trainerDone && (
            <div className="mt-1.5 flex items-center justify-center gap-4">
              <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
                {label}
              </p>
              {!error && deck !== null && !emptyPool && (
                <button
                  type="button"
                  onClick={() => setPacksOpen(true)}
                  className="font-body text-[11px] font-black uppercase tracking-[0.22em] text-flag-red transition-colors hover:text-ink"
                >
                  + Add Cards
                </button>
              )}
            </div>
          )}
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            Couldn&apos;t load your deck — is the backend running?
          </p>
        ) : deck === null ? null : emptyPool ? (
          /* First-run state: no cards yet — everything funnels into the popup. */
          <div className="text-center">
            <h2 className="font-display text-[clamp(26px,5vw,36px)] font-black leading-tight tracking-tight text-ink">
              Your pool is empty.
            </h2>
            <p className="mx-auto mt-3 max-w-[360px] font-body text-[15px] leading-relaxed text-ink-soft">
              Grab a pack of words — by level or by situation — or forge your
              own, and start building sentences.
            </p>
            <button
              type="button"
              onClick={() => setPacksOpen(true)}
              className="btn-3d mt-7 inline-flex items-center gap-2 rounded-[20px] border-[3px] border-flag-red-deep bg-flag-red px-7 py-3.5 font-display text-[15px] font-black uppercase tracking-[0.16em] text-on-fill"
              style={redShadow}
            >
              + Add Cards
            </button>
          </div>
        ) : (
          <VocabTrainer
            deck={deck}
            onRemove={handleRemove}
            onAttempt={handleAttempt}
            onReveal={handleReveal}
            onExplain={handleExplain}
            onFlag={handleFlag}
            newAllowance={newAllowance}
            reviewCap={REVIEW_CAP}
            roundTarget={roundTarget ?? undefined}
            onDoneChange={setTrainerDone}
            onGenderMiss={handleGenderMiss}
            onGenderCues={handleGenderCues}
            onGloss={handleGloss}
            onAdd={handleAddWord}
          />
        )}

        {packsOpen && deck !== null && (
          <PackModal
            token={token}
            onPoolChanged={refreshDeck}
            onUnauthorized={signOut}
            onClose={() => setPacksOpen(false)}
            canPractice={deck.length > 0}
          />
        )}
      </main>
    </div>
  );
}
