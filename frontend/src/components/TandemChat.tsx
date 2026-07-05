"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import ConversationView from "./ConversationView";
import TopicScreen from "./TopicScreen";
import { useAuth } from "./auth/AuthContext";

// Grammatik-Tandem orchestrator (TANDEM-001) — the /tandem counterpart to
// VoiceChat. Same auth guard, but instead of the lesson/voice SetupView it
// shows a topic picker, then hands a fixed tandem session to the shared
// ConversationView. Lena is one continuous character (D2), so there is no
// voice picker — the persona owns its voice.
const TANDEM_VOICE = "German_Female";

export default function TandemChat() {
  const { token, ready } = useAuth();
  const router = useRouter();
  const [topic, setTopic] = useState<string | null>(null);

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
  // lesson=tandem. Finishing returns to the topic picker for a fresh session.
  if (topic === null) {
    return <TopicScreen onStart={setTopic} />;
  }

  return (
    <ConversationView
      params={{ lesson: "tandem", voice: TANDEM_VOICE, topic }}
      onFinish={() => setTopic(null)}
    />
  );
}
