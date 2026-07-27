"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ConversationView from "./ConversationView";
import TopicScreen from "./TopicScreen";
import { useAuth } from "./auth/AuthContext";
import {
  addWord,
  fetchGloss,
  UnauthorizedError,
  type GlossInfo,
} from "./satzschmiede/api";

// Grammatik-Tandem orchestrator (TANDEM-001) — the /tandem counterpart to
// VoiceChat. Same auth guard, but instead of the lesson/voice SetupView it
// shows a topic picker, then hands a fixed tandem session to the shared
// ConversationView. Lena is one continuous character (D2), so there is no
// voice picker — the persona owns its voice.
const TANDEM_VOICE = "German_Female";

export default function TandemChat() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();
  const [topic, setTopic] = useState<string | null>(null);

  // UI-009: word-gloss popover wiring for Lena's chat bubbles — same
  // auth-guarded pattern as Sprechen.tsx's handleGloss/handleAddWord, with
  // its own OBS-007 practice-session id (minted lazily on first gloss).
  const glossSessionRef = useRef<string | null>(null);

  const handleGloss = useCallback(
    async (word: string, context: string): Promise<GlossInfo> => {
      if (!token) throw new UnauthorizedError("/satz/gloss");
      glossSessionRef.current ??=
        "tandem-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await fetchGloss(token, word, context, glossSessionRef.current);
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
        // SATZ-013: gloss-popover add — counts against the daily gloss cap.
        const res = await addWord(
          token,
          lemma,
          glossSessionRef.current ?? undefined,
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

  // Same guard the /learn route uses: once hydration settles, a missing token
  // bounces to the public landing page, where sign-in lives.
  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  // Don't flash the picker before auth is known, nor for a signed-out visitor
  // mid-redirect.
  if (!ready || !token) {
    return null;
  }

  // Pick a topic first; then drop into the shared conversation view with
  // lesson=tandem.
  if (topic === null) {
    return <TopicScreen onStart={setTopic} />;
  }

  return (
    <ConversationView
      params={{ lesson: "tandem", voice: TANDEM_VOICE, topic }}
      // Debrief modal's "Back to modes" button promises /practice (BUG-008) —
      // leave /tandem, don't just reset topic state. Backing out of the
      // briefing before the call starts still returns to the topic picker.
      onFinish={() => router.push("/practice")}
      onBack={() => setTopic(null)}
      onGloss={handleGloss}
      onAdd={handleAddWord}
    />
  );
}
