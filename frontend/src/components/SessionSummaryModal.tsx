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

interface PronEval {
  aggregate: { pron_score: number };
}

interface SessionData {
  ended_at: string | null;
  passed: boolean | null;
  goal_eval: GoalEval | null;
  pron_eval: PronEval | null;
}

type EvalStatus = "loading" | "ready" | "timeout" | "no-id";

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
          <h2 className={`text-2xl font-bold ${styles.text}`}>{title}</h2>
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
          <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">
            Evaluation
          </div>
          Not evaluated — this lesson isn&apos;t scored.
        </div>
      )}
      {hasPron && <PronEvalBlock evalData={data.pron_eval as PronEval} />}
    </div>
  );
}

function GoalEvalBlock({ evalData }: { evalData: GoalEval }) {
  const passBadge = evalData.passed
    ? { dot: "bg-green-400", text: "text-green-400", label: "Passed" }
    : { dot: "bg-red-400", text: "text-red-400", label: "Not passed" };

  return (
    <div className="rounded-lg bg-slate-900 p-4">
      <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-500">
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
            <div className="ml-5 text-xs text-slate-400">
              <span className="text-slate-500">You said: </span>
              {formatEvidence(g.evidence)}
            </div>
            {g.reasoning && (
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

function PronEvalBlock({ evalData }: { evalData: PronEval }) {
  const score = Math.round(evalData.aggregate.pron_score);
  return (
    <div className="rounded-lg bg-slate-900 p-4">
      <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-500">
        Pronunciation
      </div>
      <div className="text-lg font-semibold text-slate-100">{score} / 100</div>
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
