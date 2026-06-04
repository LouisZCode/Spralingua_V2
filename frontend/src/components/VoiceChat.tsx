"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import SetupView, { type SessionParams } from "./SetupView";
import ConversationView from "./ConversationView";
import { useAuth } from "./auth/AuthContext";

export default function VoiceChat() {
  const { token, ready } = useAuth();
  const router = useRouter();
  const [params, setParams] = useState<SessionParams | null>(null);

  // Guard the /learn route directly — someone could deep-link past the landing
  // CTA. Once hydration finishes, a missing token bounces to the public landing
  // page, where the Google sign-in flow lives.
  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  // Don't flash the setup UI before auth state is known, nor render it for a
  // signed-out visitor mid-redirect.
  if (!ready || !token) {
    return null;
  }

  if (!params) {
    return <SetupView onSubmit={setParams} />;
  }

  return (
    <ConversationView params={params} onFinish={() => setParams(null)} />
  );
}
