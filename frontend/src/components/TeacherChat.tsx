"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import ConversationView from "./ConversationView";
import { useAuth } from "./auth/AuthContext";
import { TEACHER_LESSON, TEACHER_VOICE } from "./shared/teacher";

// AGENT-001: the /teacher orchestrator — the explanation-agent counterpart to
// TandemChat, minus its picker screens: there is one teacher (Clara) with one
// voice and no topic, so an auth guard straight into the shared
// ConversationView is the whole component. `typedInput` surfaces the
// type-a-turn overlay as a first-class button — typing is the precise channel
// for German examples, since the teacher session runs English STT.

export default function TeacherChat() {
  const { token, ready } = useAuth();
  const router = useRouter();

  // Same guard the /learn and /tandem routes use: once hydration settles, a
  // missing token bounces to the public landing page, where sign-in lives.
  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  if (!ready || !token) {
    return null;
  }

  return (
    <ConversationView
      params={{ lesson: TEACHER_LESSON, voice: TEACHER_VOICE }}
      typedInput
      onFinish={() => router.push("/practice")}
      onBack={() => router.push("/practice")}
    />
  );
}
