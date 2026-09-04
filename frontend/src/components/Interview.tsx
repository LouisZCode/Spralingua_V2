"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import ItemPicker from "./interview/ItemPicker";
import InterviewPlayer from "./interview/InterviewPlayer";
import {
  fetchAudioUrl,
  fetchItem,
  fetchItems,
  submitAnswer,
  submitComprehension,
  UnauthorizedError,
  type AnswerResult,
  type ComprehensionResult,
  type InterviewItem,
  type InterviewItemSummary,
} from "./interview/api";
import AppHeader from "@/components/shared/AppHeader";

// Interview — INTV-003 slice 3: the production frontend for the personal
// audio pool exercise. A real interview chunk, heard cold and retold
// (round 1, content-only), then read and answered out loud (round 2,
// grammar + "the German way" + goal coverage). This component is the
// auth-guarded page shell + item list/detail fetch, mirroring Sprechen.tsx
// and Satzschmiede.tsx: it owns the token and every network call, and hands
// plain callback props down to the trainer components, which own none of
// the auth machinery themselves.
//
// A dev door on /development (MVP-001) — not one of the four surfaces
// /practice reaches.
export default function Interview() {
  const { token, ready, expireSession } = useAuth();
  const router = useRouter();

  const [items, setItems] = useState<InterviewItemSummary[] | null>(null); // null = loading
  const [itemsError, setItemsError] = useState(false);

  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [item, setItem] = useState<InterviewItem | null>(null); // null = loading
  const [itemError, setItemError] = useState(false);

  // OBS-007: minted lazily on the first attempt (round 1 or round 2,
  // whichever fires first — the same item's two rounds are one sitting),
  // held for the whole page visit — same convention as every other drill
  // shell's practiceSessionRef (Sprechen.tsx, Szenario.tsx, …).
  const sessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    fetchItems(token)
      .then((list) => {
        if (!cancelled) setItems(list);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof UnauthorizedError) {
          expireSession();
        } else {
          setItemsError(true);
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    if (!token || !selectedItemId) {
      setItem(null);
      return;
    }
    let cancelled = false;
    setItem(null);
    setItemError(false);
    fetchItem(token, selectedItemId)
      .then((full) => {
        if (!cancelled) setItem(full);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof UnauthorizedError) {
          expireSession();
        } else {
          setItemError(true);
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, selectedItemId]);

  const handleFetchAudioUrl = useCallback(
    async (chunkId: string): Promise<string> => {
      if (!token) throw new UnauthorizedError("/interview/audio");
      try {
        return await fetchAudioUrl(token, chunkId);
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession]
  );

  const handleComprehension = useCallback(
    async (chunkId: string, audio: Blob): Promise<ComprehensionResult> => {
      if (!token) throw new UnauthorizedError("/interview/comprehension");
      sessionIdRef.current ??= "interview-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await submitComprehension(token, chunkId, audio, sessionIdRef.current);
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
        throw e;
      }
    },
    [token, expireSession]
  );

  const handleAnswer = useCallback(
    async (chunkId: string, audio: Blob): Promise<AnswerResult> => {
      if (!token) throw new UnauthorizedError("/interview/answer");
      sessionIdRef.current ??= "interview-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await submitAnswer(token, chunkId, audio, sessionIdRef.current);
      } catch (e) {
        if (e instanceof UnauthorizedError) expireSession();
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

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col px-6 py-12">
        {selectedItemId === null ? (
          <ItemPicker items={items} error={itemsError} onPick={setSelectedItemId} />
        ) : (
          <InterviewPlayer
            key={selectedItemId}
            item={item}
            error={itemError}
            onBack={() => setSelectedItemId(null)}
            onFetchAudioUrl={handleFetchAudioUrl}
            onComprehension={handleComprehension}
            onAnswer={handleAnswer}
          />
        )}
      </main>
    </div>
  );
}
