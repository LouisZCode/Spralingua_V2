"use client";

import { useState } from "react";
import SetupView, { type SessionParams } from "./SetupView";
import ConversationView from "./ConversationView";

export default function VoiceChat() {
  const [params, setParams] = useState<SessionParams | null>(null);

  if (!params) {
    return <SetupView onSubmit={setParams} />;
  }

  return (
    <ConversationView
      params={params}
      onFinish={() => setParams(null)}
    />
  );
}
