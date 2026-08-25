"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ConversationView from "./ConversationView";
import TeacherTopicScreen from "./TeacherTopicScreen";
import ExerciseCard, {
  type ExerciseData,
  type ExerciseVerdict,
} from "./teacher/ExerciseCard";
import { useAuth } from "./auth/AuthContext";
import { TEACHER_LESSON, TEACHER_VOICE } from "./shared/teacher";
import { HTTP_BASE } from "@/lib/api";

// AGENT-001: the /teacher orchestrator — the explanation-agent counterpart to
// TandemChat. Note 5 added a picker screen (TeacherTopicScreen) ahead of the
// shared ConversationView: auth guard -> topic picker -> conversation, so
// Clara opens inside a topic the app already knew instead of asking for one.
// `typedInput` surfaces the type-a-turn overlay as a first-class button —
// typing is the precise channel for German examples, since the teacher
// session runs English STT.
//
// AGENT-00X: this file also owns Clara's interactive-exercise loop —
// GET the item, render ExerciseCard, POST the attempt, and report the
// outcome back through the pipeline via the existing POST /say/{user_id}.
// See ConversationView's onExerciseRequest/onBotReply/onSessionEnded props
// for the three hooks this relies on, and teacher/routes.py (backend) for
// the two HTTP endpoints.

// Sentinel-OUT: kept byte-identical to agents/pipecat_wrapper.py's
// EXERCISE_RESULT_PREFIX. The backend keys off this exact literal to route a
// /say turn out of the stored transcript and the audio↔text pairing (see
// that file's "AGENT-00X sentinel-IN" comment) — changing one side without
// the other breaks that routing.
const EXERCISE_RESULT_PREFIX = "⟦ÜBUNGSERGEBNIS⟧";

// The backend caps /say bodies at 500 chars (config/settings.py's
// SAY_MAX_CHARS); stay comfortably under that so a report is never the thing
// that gets rejected.
const REPORT_MAX_CHARS = 450;

// Auto-dismiss a graded verdict after this long even if Clara's reply to the
// report never arrives (judge timeout, a skipped follow-up, etc).
const VERDICT_AUTOCLOSE_MS = 8000;

function truncateReport(text: string): string {
  return text.length > REPORT_MAX_CHARS
    ? text.slice(0, REPORT_MAX_CHARS - 1) + "…"
    : text;
}

type RawExercise = {
  drill: string;
  itemId: string;
  patternId: string;
  instruction: string;
  prompt: string;
};

export default function TeacherChat() {
  const { token, user, ready } = useAuth();
  const router = useRouter();
  // null = still picking; "" is a valid choice (the "just want to talk"
  // escape hatch), so the gate below is `=== null`, not falsiness.
  const [topic, setTopic] = useState<string | null>(null);
  const handleTopicStart = useCallback((t: string) => {
    setTopic(t);
  }, []);

  // Same guard the /learn and /tandem routes use: once hydration settles, a
  // missing token bounces to the public landing page, where sign-in lives.
  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  // ─── AGENT-00X: the exercise loop ────────────────────────────────────
  const [exercise, setExercise] = useState<ExerciseData | null>(null);
  // Bumps on every accepted exercise_request so ExerciseCard remounts with
  // fresh internal state (answering, not a stale verdict) even if a marker
  // ever repeated the same pattern id back to back.
  const [exerciseKey, setExerciseKey] = useState(0);
  // Guards a fetch/POST in flight from a request that's since been
  // superseded (a NEW exercise_request, or the session ending) — only the
  // most recent request's resolution is allowed to touch state.
  const requestSeqRef = useRef(0);
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // True only once a verdict is showing for the CURRENT card — gates
  // onBotReply's auto-dismiss so an unanswered card (the learner is
  // ignoring it and still chatting) doesn't vanish out from under them.
  const verdictShownRef = useRef(false);

  const clearDismissTimer = useCallback(() => {
    if (dismissTimerRef.current) {
      clearTimeout(dismissTimerRef.current);
      dismissTimerRef.current = null;
    }
  }, []);

  const sendReport = useCallback(
    async (rawText: string) => {
      if (!token || !user) return;
      try {
        await fetch(`${HTTP_BASE}/say/${encodeURIComponent(user.id)}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ text: truncateReport(rawText) }),
        });
      } catch {
        // Best-effort — Clara simply never learns how it went. Nothing here
        // is worth surfacing to the learner; the graded verdict itself
        // already rendered from the /teacher/exercise/attempts response.
      }
    },
    [token, user]
  );

  const handleExerciseRequest = useCallback(
    (patternId: string) => {
      // A fresh request always replaces whatever card is open.
      const seq = ++requestSeqRef.current;
      clearDismissTimer();
      verdictShownRef.current = false;
      setExercise(null);
      if (!token) return;
      fetch(
        `${HTTP_BASE}/teacher/exercise?pattern=${encodeURIComponent(patternId)}`,
        { headers: { Authorization: `Bearer ${token}` } }
      )
        .then(async (res) => {
          if (requestSeqRef.current !== seq) return; // superseded
          if (res.status === 404) {
            void sendReport(
              `${EXERCISE_RESULT_PREFIX} No exercise was available for pattern ${patternId}.`
            );
            return;
          }
          if (!res.ok) return; // nothing actionable to show or report
          const data: RawExercise = await res.json();
          setExercise({
            drill: data.drill,
            itemId: data.itemId,
            patternId: data.patternId,
            instruction: data.instruction,
            prompt: data.prompt,
          });
          setExerciseKey((k) => k + 1);
        })
        .catch(() => {
          // Network error fetching the item — same "nothing actionable" call
          // as a non-ok, non-404 response.
        });
    },
    [token, sendReport, clearDismissTimer]
  );

  const handleAnswer = useCallback(
    async (data: ExerciseData, answer: string): Promise<ExerciseVerdict> => {
      if (!token) throw new Error("Not signed in.");
      const res = await fetch(`${HTTP_BASE}/teacher/exercise/attempts`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          drill: data.drill,
          itemId: data.itemId,
          answer,
        }),
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => null))?.detail;
        throw new Error(
          typeof detail === "string"
            ? detail
            : "Couldn't check that — try again."
        );
      }
      const verdict: ExerciseVerdict = await res.json();
      const verb = verdict.correct ? "Correct" : "Wrong";
      let report =
        `${EXERCISE_RESULT_PREFIX} ${verb} — exercise on ${data.patternId}: ` +
        `they answered "${answer}", expected "${verdict.expected}".`;
      if (verdict.note) report += ` ${verdict.note}`;
      void sendReport(report);

      // Verdict is now visible — arm the dismissal window (see
      // onBotReply/onSessionEnded below for the other two triggers).
      verdictShownRef.current = true;
      clearDismissTimer();
      dismissTimerRef.current = setTimeout(() => {
        dismissTimerRef.current = null;
        verdictShownRef.current = false;
        setExercise(null);
      }, VERDICT_AUTOCLOSE_MS);

      return verdict;
    },
    [token, sendReport, clearDismissTimer]
  );

  const handleSkip = useCallback(() => {
    if (!exercise) return;
    requestSeqRef.current++; // supersede any fetch that might still land
    void sendReport(
      `${EXERCISE_RESULT_PREFIX} They skipped the exercise on ${exercise.patternId}.`
    );
    clearDismissTimer();
    verdictShownRef.current = false;
    setExercise(null);
  }, [exercise, sendReport, clearDismissTimer]);

  // A reply landed (any turn, not necessarily one carrying a new exercise).
  // Only acts while a VERDICT is showing — an unanswered card stays open
  // across Clara's other chat replies, since the card is explicitly meant
  // to not block the conversation.
  const handleBotReply = useCallback(() => {
    if (!verdictShownRef.current) return;
    requestSeqRef.current++;
    clearDismissTimer();
    verdictShownRef.current = false;
    setExercise(null);
  }, [clearDismissTimer]);

  // Session winding down (WS closed / Finish confirmed) — drop any open
  // card silently, whatever state it's in.
  const handleSessionEnded = useCallback(() => {
    requestSeqRef.current++;
    clearDismissTimer();
    verdictShownRef.current = false;
    setExercise(null);
  }, [clearDismissTimer]);

  // Belt-and-suspenders: clear the auto-dismiss timer if this component
  // itself unmounts mid-window (e.g. a fast route change).
  useEffect(() => clearDismissTimer, [clearDismissTimer]);
  // ───────────────────────────────────────────────────────────────────

  if (!ready || !token) {
    return null;
  }

  if (topic === null) {
    return <TeacherTopicScreen onStart={handleTopicStart} />;
  }

  return (
    <>
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
        onExerciseRequest={handleExerciseRequest}
        onBotReply={handleBotReply}
        onSessionEnded={handleSessionEnded}
      />
      {/* AGENT-00X: floats over the live chat, never blocks it — no
          backdrop, and the wrapper itself passes clicks through everywhere
          but the card. Anchored below the header (Clara's room renders the
          prominent "End conversation" button there — see ConversationView's
          prominentFinish) so this can never cover it; it lands in the orb
          hero's decorative space instead, well above the Record/Type
          controls and the transcript further down. */}
      {exercise && (
        <div className="pointer-events-none fixed inset-x-0 top-24 z-30 flex justify-center px-4 sm:top-28">
          <ExerciseCard
            key={exerciseKey}
            data={exercise}
            onSubmit={(answer) => handleAnswer(exercise, answer)}
            onSkip={handleSkip}
          />
        </div>
      )}
    </>
  );
}
