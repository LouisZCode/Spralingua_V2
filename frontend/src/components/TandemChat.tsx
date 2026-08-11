"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ConversationView from "./ConversationView";
import PartnerScreen from "./PartnerScreen";
import TopicScreen from "./TopicScreen";
import { useAuth } from "./auth/AuthContext";
import {
  addWord,
  fetchGloss,
  UnauthorizedError,
  type GlossInfo,
} from "./satzschmiede/api";
import { PARTNERS, type PartnerId } from "./shared/tandem";

// Grammatik-Tandem orchestrator (TANDEM-001 / TAND-008) — the /tandem
// counterpart to VoiceChat. Same auth guard, but instead of the lesson/voice
// SetupView it runs partner picker → topic picker → the shared
// ConversationView. Each partner is one continuous character (D2), so there
// is no voice picker — the persona owns its voice (shared/tandem.ts).

export default function TandemChat() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();
  const [partnerId, setPartnerId] = useState<PartnerId | null>(null);
  const [topic, setTopic] = useState<string | null>(null);
  // TAND-003: Natural (streaming VAD) vs Practice (tap-record) input mode,
  // picked on TopicScreen alongside the topic itself.
  const [practiceMode, setPracticeMode] = useState(false);
  // TAND-012: chat-length picker (5/10/15 exchanges), also picked on
  // TopicScreen — rides the WS URL as `&exchanges=` via SessionParams.
  const [exchanges, setExchanges] = useState(10);

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

  // TAND-008: pick a partner first, then a topic (+ input mode), then drop
  // into the shared conversation view with that partner's lesson + voice.
  if (partnerId === null) {
    return <PartnerScreen onPick={setPartnerId} />;
  }
  const partner = PARTNERS[partnerId];

  if (topic === null) {
    return (
      <TopicScreen
        partner={partner}
        onStart={(t, mode, ex) => {
          setPracticeMode(mode === "practice");
          setExchanges(ex);
          setTopic(t);
        }}
        onSwitchPartner={() => setPartnerId(null)}
      />
    );
  }

  return (
    <ConversationView
      params={{ lesson: partner.lessonId, voice: partner.voice, topic, exchanges }}
      practiceMode={practiceMode}
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
