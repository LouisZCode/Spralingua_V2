"use client";

// Post-session summary modal. Mounted by ConversationView once the
// WebSocket has closed (whether the agent ended the call or the user
// clicked Finish Lesson). Designed to be reusable — a future Profile
// view should be able to render the same content inline without the
// modal wrapper, just by extracting the inner card.
//
// EVAL-UI-001: the modal now also polls GET /sessions/{sessionId} for
// the goal + pronunciation evaluator results captured at disconnect.
// Polling is driven by the WS-vs-finalize race: backend evaluators run
// in pipeline/factory.py's finally: block AFTER the WS closes, so the
// row's `ended_at` is briefly NULL when the modal opens. We poll until
// `ended_at` is set (or 30s timeout) and then render.

import { useEffect, useState } from "react";

export type CompletionStatus = "success" | "info" | "warning";

export interface CompletionData {
  title?: string;
  status?: CompletionStatus;
  message_by_agent?: string;
  message_by_user?: string;
}

interface GoalRow {
  goal: string;
  passed: boolean;
  evidence: string;
  reasoning: string;
}

interface GoalEval {
  score: number;
  pass_threshold: number;
  passed: boolean;
  goals: GoalRow[];
}

interface PronWord {
  word: string;
  accuracy_score: number;
  error_type: string;
}

interface PronTurn {
  text: string;
  recognized: string;
  accuracy_score: number;
  fluency_score: number;
  completeness_score: number;
  prosody_score: number | null;
  pron_score: number;
  words: PronWord[];
}

interface PronAggregate {
  pron_score: number;
  accuracy_score: number;
  fluency_score: number;
  completeness_score: number;
  prosody_score: number | null;
  turns_assessed: number;
}

interface PronEval {
  locale: string;
  turns: PronTurn[];
  aggregate: PronAggregate;
}

interface SessionData {
  lesson_id: string;
  ended_at: string | null;
  passed: boolean | null;
  goal_eval: GoalEval | null;
  pron_eval: PronEval | null;
}

type EvalStatus = "loading" | "ready" | "timeout" | "no-id";

// CEFR-level → which pronunciation sub-scores + per-word details to render.
// Sub-scores like Prosody are unreliable on the short utterances A1 learners
// produce (Azure can't grade intonation from a single word), so we hide them
// until the level produces enough audio for the score to mean something.
type Level = "A1" | "A2" | "B1" | "B2" | "C1" | "C2";

interface PronSchema {
  showFluency: boolean;
  showCompleteness: boolean;
  showProsody: boolean;
}

const PRON_SCHEMA: Record<Level, PronSchema> = {
  A1: { showFluency: false, showCompleteness: false, showProsody: false },
  A2: { showFluency: true,  showCompleteness: false, showProsody: false },
  B1: { showFluency: true,  showCompleteness: true,  showProsody: false },
  B2: { showFluency: true,  showCompleteness: true,  showProsody: true  },
  C1: { showFluency: true,  showCompleteness: true,  showProsody: true  },
  C2: { showFluency: true,  showCompleteness: true,  showProsody: true  },
};

// Per-word callout thresholds — surface only the standouts, not every word.
const BEST_THRESHOLD = 80;   // top word shown only if its accuracy >= this
const WORST_THRESHOLD = 30;  // worst word shown only if its accuracy < this

function parseLevel(lessonId: string): Level {
  const m = lessonId.match(/^([abc][12])_/i);
  if (!m) return "A1";
  return m[1].toUpperCase() as Level;
}

const HTTP_BASE = "http://localhost:8765";
const POLL_INTERVAL_MS = 1000;
const POLL_TIMEOUT_MS = 30000;

const DEFAULT_TITLE = "Session complete";
const DEFAULT_STATUS: CompletionStatus = "info";
const DEFAULT_MESSAGE_BY_AGENT = "The session ended.";
const DEFAULT_MESSAGE_BY_USER = "You ended this session.";

const STATUS_STYLES: Record<
  CompletionStatus,
  { dot: string; text: string }
> = {
  success: { dot: "bg-green-400", text: "text-green-400" },
  info: { dot: "bg-blue-400", text: "text-blue-400" },
  warning: { dot: "bg-amber-400", text: "text-amber-400" },
};

export default function SessionSummaryModal({
  lessonTitle,
  completion,
  endedBy,
  sessionId,
  onClose,
}: {
  lessonTitle: string;
  completion: CompletionData | null;
  endedBy: "user" | "agent";
  sessionId: string | null;
  onClose: () => void;
}) {
  // The parent (ConversationView) only mounts this component when
  // showSummary flips true, so an unmount/remount handles state reset for
  // free — no need for an open-transition guard.
  const [sessionData, setSessionData] = useState<SessionData | null>(null);
  const [evalStatus, setEvalStatus] = useState<EvalStatus>(() =>
    sessionId ? "loading" : "no-id"
  );

  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (cancelled) return;
      try {
        const r = await fetch(`${HTTP_BASE}/sessions/${sessionId}`);
        if (!r.ok) return;
        const data = (await r.json()) as SessionData;
        if (cancelled) return;
        if (data.ended_at !== null) {
          setSessionData(data);
          setEvalStatus("ready");
          if (intervalId) clearInterval(intervalId);
          if (timeoutId) clearTimeout(timeoutId);
        }
      } catch {
        // Swallow transient errors; keep polling until timeout.
      }
    };

    void tick(); // immediate first fetch; the row might already be finalized
    intervalId = setInterval(() => void tick(), POLL_INTERVAL_MS);
    timeoutId = setTimeout(() => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      setEvalStatus((prev) => (prev === "ready" ? prev : "timeout"));
    }, POLL_TIMEOUT_MS);

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [sessionId]);

  const title = completion?.title ?? DEFAULT_TITLE;
  const status = completion?.status ?? DEFAULT_STATUS;
  const message =
    endedBy === "user"
      ? completion?.message_by_user ?? DEFAULT_MESSAGE_BY_USER
      : completion?.message_by_agent ?? DEFAULT_MESSAGE_BY_AGENT;
  const endedByLabel =
    endedBy === "user" ? "Ended by you." : "Ended by the agent.";
  const styles = STATUS_STYLES[status];

  return (
    // Backdrop locks the chat underneath — no click-through, no Esc, no
    // X icon. Single explicit action below. We keep dismissal explicit
    // because the eval block below the message is what the user came to
    // read.
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-slate-800 p-8 shadow-2xl">
        {lessonTitle && (
          <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">
            {lessonTitle}
          </div>
        )}

        <div className="mb-4 flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${styles.dot}`} />
          <h2 className={`text-xl font-bold ${styles.text}`}>{title}</h2>
        </div>

        <p className="mb-3 text-xs uppercase tracking-wide text-slate-500">
          {endedByLabel}
        </p>

        <p className="mb-6 whitespace-pre-line text-base text-slate-200">
          {message.trim()}
        </p>

        <EvalSection status={evalStatus} data={sessionData} />

        <button
          onClick={onClose}
          className="mt-8 w-full rounded-lg bg-green-500 py-3 font-semibold text-slate-900 hover:bg-green-400"
        >
          Back to lessons
        </button>
      </div>
    </div>
  );
}

function EvalSection({
  status,
  data,
}: {
  status: EvalStatus;
  data: SessionData | null;
}) {
  if (status === "loading") {
    return (
      <div className="rounded-lg bg-slate-900 p-4">
        <div className="flex items-center gap-3 text-sm text-slate-400">
          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-slate-500 border-t-slate-200" />
          Analyzing your session…
        </div>
      </div>
    );
  }

  if (status === "no-id" || status === "timeout") {
    return (
      <div className="rounded-lg bg-slate-900 p-4 text-sm text-slate-400">
        Results unavailable right now.
      </div>
    );
  }

  // status === "ready"
  if (!data) return null;

  const hasGoals = data.goal_eval !== null;
  const hasPron = data.pron_eval !== null;

  return (
    <div className="space-y-4">
      {hasGoals ? (
        <GoalEvalBlock evalData={data.goal_eval as GoalEval} />
      ) : (
        <div className="rounded-lg bg-slate-900 p-4 text-sm text-slate-300">
          <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">
            Evaluation
          </div>
          Not evaluated — this lesson isn&apos;t scored.
        </div>
      )}
      {hasPron && (
        <PronEvalBlock
          evalData={data.pron_eval as PronEval}
          lessonId={data.lesson_id}
        />
      )}
    </div>
  );
}

function GoalEvalBlock({ evalData }: { evalData: GoalEval }) {
  const passBadge = evalData.passed
    ? { dot: "bg-green-400", text: "text-green-400", label: "Passed" }
    : { dot: "bg-red-400", text: "text-red-400", label: "Not passed" };

  return (
    <div className="rounded-lg bg-slate-900 p-4">
      <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
        Goal evaluation
      </div>
      <div className="mb-4 flex items-center gap-3">
        <span className="text-lg font-semibold text-slate-100">
          {evalData.score} / 100
        </span>
        <span className="flex items-center gap-1.5 text-sm">
          <span className={`h-2 w-2 rounded-full ${passBadge.dot}`} />
          <span className={passBadge.text}>{passBadge.label}</span>
        </span>
      </div>

      <ul className="space-y-3">
        {evalData.goals.map((g, i) => (
          <li key={i} className="border-l-2 border-slate-700 pl-3">
            <div className="mb-1 flex items-start gap-2">
              <span
                className={
                  g.passed ? "text-green-400" : "text-red-400"
                }
                aria-hidden
              >
                {g.passed ? "✓" : "✗"}
              </span>
              <span className="text-sm font-medium text-slate-100">
                {g.goal}
              </span>
            </div>
            <div className="ml-5 text-sm text-slate-400">
              <span className="text-slate-500">You said: </span>
              {formatEvidence(g.evidence)}
            </div>
            {/* Reasoning is the "what to fix" hint — only useful when the
                goal failed. On a pass, the evidence already tells the story. */}
            {!g.passed && g.reasoning && (
              <div className="ml-5 mt-1 text-xs italic text-slate-400">
                {g.reasoning}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function PronEvalBlock({
  evalData,
  lessonId,
}: {
  evalData: PronEval;
  lessonId: string;
}) {
  const schema = PRON_SCHEMA[parseLevel(lessonId)];
  const agg = evalData.aggregate;

  const allWords = evalData.turns.flatMap((t) => t.words);

  // Only surface standouts. With a single word the headline IS that word's
  // score, so showing a duplicate "best/worst" line is pointless.
  let bestWord: PronWord | null = null;
  let worstWord: PronWord | null = null;
  if (allWords.length > 1) {
    const sorted = [...allWords].sort(
      (a, b) => b.accuracy_score - a.accuracy_score,
    );
    const top = sorted[0];
    const bottom = sorted[sorted.length - 1];
    if (top.accuracy_score >= BEST_THRESHOLD) bestWord = top;
    if (bottom.accuracy_score < WORST_THRESHOLD) worstWord = bottom;
  }

  const hasSubScores =
    schema.showFluency || schema.showCompleteness || schema.showProsody;

  return (
    <div className="rounded-lg bg-slate-900 p-4">
      <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
        Pronunciation
      </div>
      <div className="mb-3 text-lg font-semibold text-slate-100">
        {Math.round(agg.accuracy_score)} / 100
      </div>

      {hasSubScores && (
        <div className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400">
          {schema.showFluency && (
            <SubScore label="Fluency" value={agg.fluency_score} />
          )}
          {schema.showCompleteness && (
            <SubScore label="Completeness" value={agg.completeness_score} />
          )}
          {schema.showProsody && agg.prosody_score !== null && (
            <SubScore label="Prosody" value={agg.prosody_score} />
          )}
        </div>
      )}

      {(bestWord || worstWord) && (
        <ul className="space-y-1 text-sm">
          {bestWord && (
            <li className="flex items-center justify-between text-slate-200">
              <span>
                <span className="text-slate-500">Top word: </span>
                {bestWord.word}
              </span>
              <span className="text-slate-400">
                {Math.round(bestWord.accuracy_score)} / 100
              </span>
            </li>
          )}
          {worstWord && (
            <li className="flex items-center justify-between text-slate-200">
              <span>
                <span className="text-slate-500">Worst word: </span>
                {worstWord.word}
              </span>
              <span className="text-slate-400">
                {Math.round(worstWord.accuracy_score)} / 100
              </span>
            </li>
          )}
        </ul>
      )}

      <div className="mt-3 text-xs italic text-slate-500">
        Anything above 80 is great.
      </div>
    </div>
  );
}

function SubScore({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between">
      <span>{label}</span>
      <span className="text-slate-300">{Math.round(value)}</span>
    </div>
  );
}

function formatEvidence(evidence: string): React.ReactNode {
  // The evaluator emits the literal string "none" when there's no relevant
  // student turn for a goal — render that as a softer placeholder.
  const trimmed = evidence.trim();
  if (!trimmed || trimmed.toLowerCase() === "none") {
    return <span className="italic text-slate-500">(no attempt)</span>;
  }
  return <span className="text-slate-300">&ldquo;{trimmed}&rdquo;</span>;
}
