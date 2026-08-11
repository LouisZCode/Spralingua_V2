"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import ConversationView from "./ConversationView";
import TeacherTopicScreen from "./TeacherTopicScreen";
import { useAuth } from "./auth/AuthContext";
import { TEACHER_LESSON, TEACHER_VOICE } from "./shared/teacher";

// AGENT-001: the /teacher orchestrator — the explanation-agent counterpart to
// TandemChat. Note 5 added a picker screen (TeacherTopicScreen) ahead of the
// shared ConversationView: auth guard -> topic picker -> conversation, so
// Clara opens inside a topic the app already knew instead of asking for one.
// `typedInput` surfaces the type-a-turn overlay as a first-class button —
// typing is the precise channel for German examples, since the teacher
// session runs English STT.

export default function TeacherChat() {
  const { token, ready } = useAuth();
  const router = useRouter();
  // null = still picking; "" is a valid choice (the "just want to talk"
  // escape hatch), so the gate below is `=== null`, not falsiness.
  const [topic, setTopic] = useState<string | null>(null);

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

  if (topic === null) {
    return <TeacherTopicScreen onStart={setTopic} />;
  }

  return (
    <ConversationView
      params={{ lesson: TEACHER_LESSON, voice: TEACHER_VOICE, topic }}
      // AGENT-001 note 1: the teacher room is never open-mic — the learner
      // needs time to think before speaking to a teacher. No natural/practice
      // toggle here on purpose, unlike TopicScreen.
      practiceMode
      typedInput
      // AGENT-001: Clara's `kickoff` key means she speaks first — lock Record
      // until her opening line finishes, see ConversationView's agentOpens.
      agentOpens
      onFinish={() => router.push("/practice")}
      // Backing out of the briefing card returns to the picker, not /practice
      // (mirrors TandemChat's onBack -> setTopic(null)).
      onBack={() => setTopic(null)}
    />
  );
}
