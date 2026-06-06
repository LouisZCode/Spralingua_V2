"use client";

// Post-session summary modal. Mounted by ConversationView once the
// WebSocket has closed (whether the agent ended the call or the user
// clicked Finish Lesson).
//
// EVAL-UI-001: the modal polls GET /sessions/{sessionId} for the goal
// + pronunciation evaluator results captured at disconnect. Polling is
// driven by the WS-vs-finalize race: backend evaluators run in
// pipeline/factory.py's finally: block AFTER the WS closes, so the row's
// `ended_at` is briefly NULL when the modal opens. We poll until
// `ended_at` is set (or 30s timeout) and then render.

import { useEffect, useState } from "react";
import { HTTP_BASE } from "@/lib/api";

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
// produce, so we hide them until the level produces enough audio for the
// score to mean something.
type Level = "A1" | "A2" | "B1" | "B2" | "C1" | "C2";

interface PronSchema {
  showFluency: boolean;
  showCompleteness: boolean;
  showProsody: boolean;
}

const PRON_SCHEMA: Record<Level, PronSchema> = {
  A1: { showFluency: false, showCompleteness: false, showProsody: false },
  A2: { showFluency: true, showCompleteness: false, showProsody: false },
  B1: { showFluency: true, showCompleteness: true, showProsody: false },
  B2: { showFluency: true, showCompleteness: true, showProsody: true },
  C1: { showFluency: true, showCompleteness: true, showProsody: true },
  C2: { showFluency: true, showCompleteness: true, showProsody: true },
};

function parseLevel(lessonId: string): Level {
  const m = lessonId.match(/^([abc][12])_/i);
  if (!m) return "A1";
  return m[1].toUpperCase() as Level;
}

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
  success: { dot: "bg-success", text: "text-success" },
  info: { dot: "bg-ink", text: "text-ink" },
  warning: { dot: "bg-flag-gold-deep", text: "text-flag-gold-deep" },
};

export default function SessionSummaryModal({
  lessonTitle,
  briefingGoal,
  completion,
  endedBy,
  sessionId,
  onClose,
}: {
  lessonTitle: string;
  // Briefing goal can be: a single sentence (string), a list mirroring
  // the eval goals 1:1 (string[]), or absent (null → fall back to eval
  // criteria text per row).
  briefingGoal?: string | string[] | null;
  completion: CompletionData | null;
  endedBy: "user" | "agent";
  sessionId: string | null;
  onClose: () => void;
}) {
  const [sessionData, setSessionData] = useState<SessionData | null>(null);
  const [evalStatus, setEvalStatus] = useState<EvalStatus>(() =>
    sessionId ? "loading" : "no-id",
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

    void tick();
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
      ? (completion?.message_by_user ?? DEFAULT_MESSAGE_BY_USER)
      : (completion?.message_by_agent ?? DEFAULT_MESSAGE_BY_AGENT);
  const styles = STATUS_STYLES[status];

  return (
    // Backdrop locks the chat underneath. No click-through, no Esc, no
    // X icon — single explicit action below.
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/55 p-4 backdrop-blur-[2px]">
      <div className="max-h-[90vh] w-full max-w-[600px] overflow-y-auto rounded-[28px] border-[3px] border-ink bg-paper-warm shadow-[0_8px_0_var(--color-ink)]">
        <div className="px-7 py-8">
          {lessonTitle && (
            <p className="font-body text-[11px] font-bold uppercase tracking-[0.32em] text-ink-muted">
              {lessonTitle}
            </p>
          )}

          <div className="mt-3 flex items-center gap-3">
            <span className={`inline-block h-3 w-3 rounded-full ${styles.dot}`} />
            <h2
              className={`font-display text-[32px] font-black leading-tight ${styles.text}`}
            >
              {title}
            </h2>
          </div>

          <p className="mt-4 whitespace-pre-line font-body text-[17px] leading-[1.55] text-ink-soft">
            {message.trim()}
          </p>

          <div className="mt-7">
            <EvalSection
              status={evalStatus}
              data={sessionData}
              briefingGoal={briefingGoal ?? null}
            />
          </div>

          <button
            onClick={onClose}
            className="btn-3d mt-8 flex w-full items-center justify-center gap-3 rounded-[24px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-4 font-display text-[16px] font-black uppercase tracking-[0.18em] text-white"
            style={
              {
                ["--shadow-color"]: "var(--color-flag-red-deep)",
              } as React.CSSProperties
            }
          >
            ← Back to lessons
          </button>
        </div>
      </div>
    </div>
  );
}

function EvalSection({
  status,
  data,
  briefingGoal,
}: {
  status: EvalStatus;
  data: SessionData | null;
  briefingGoal: string | string[] | null;
}) {
  if (status === "loading") {
    return (
      <div className="rounded-[22px] border-[3px] border-ink bg-white px-5 py-4">
        <div className="flex items-center gap-3 font-body text-[14px] text-ink-soft">
          <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink-faint border-t-ink" />
          Analyzing your session…
        </div>
      </div>
    );
  }

  if (status === "no-id" || status === "timeout") {
    return (
      <div className="rounded-[22px] border-[3px] border-ink bg-white px-5 py-4 font-body text-[14px] text-ink-muted">
        Results unavailable right now.
      </div>
    );
  }

  if (!data) return null;

  const hasGoals = data.goal_eval !== null;
  const hasPron = data.pron_eval !== null;

  return (
    <div className="space-y-4">
      {hasGoals ? (
        <GoalEvalBlock
          evalData={data.goal_eval as GoalEval}
          briefingGoal={briefingGoal}
        />
      ) : (
        <div className="rounded-[22px] border-[3px] border-ink bg-white px-5 py-4">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.28em] text-ink-muted">
            Evaluation
          </p>
          <p className="mt-2 font-body text-[14px] text-ink-soft">
            Not evaluated — this lesson isn&apos;t scored.
          </p>
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

function GoalEvalBlock({
  evalData,
  briefingGoal,
}: {
  evalData: GoalEval;
  briefingGoal: string | string[] | null;
}) {
  const passed = evalData.passed;

  // Resolve the per-tile display text. Briefing copy is user-friendly;
  // eval g.goal is the technical criterion. Use briefing when shape matches:
  //   - briefingGoal is a string → use for the single eval goal (1:1)
  //   - briefingGoal is a list with same length as eval goals → use per index
  //   - else → fall back to g.goal for that row
  const displayGoalAt = (i: number, fallback: string): string => {
    if (
      Array.isArray(briefingGoal) &&
      briefingGoal.length === evalData.goals.length
    ) {
      const item = briefingGoal[i]?.trim();
      if (item) return item;
    } else if (
      typeof briefingGoal === "string" &&
      briefingGoal.trim().length > 0 &&
      evalData.goals.length === 1
    ) {
      return briefingGoal.trim();
    }
    return fallback;
  };

  return (
    <div className="rounded-[22px] border-[3px] border-ink bg-white px-5 py-5">
      <div className="flex items-center gap-3">
        <span className="font-body text-[11px] font-bold uppercase tracking-[0.32em] text-ink">
          Goal evaluation
        </span>
        <span className="h-px flex-1 bg-rule" />
      </div>

      <div className="mt-3 flex items-baseline gap-3">
        <span className="font-display text-[44px] font-black leading-none text-ink">
          {evalData.score}
        </span>
        <span className="font-display text-[18px] font-bold text-ink-muted">
          / 100
        </span>
        <span className="ml-2">
          <PassBadge passed={passed} />
        </span>
      </div>

      <ul className="mt-5 space-y-5">
        {evalData.goals.map((g, i) => {
          const displayGoal = displayGoalAt(i, g.goal);
          return (
            <li
              key={i}
              className={`rounded-2xl border-2 px-5 py-4 ${
                g.passed
                  ? "border-success/35 bg-success-soft/40"
                  : "border-flag-red/45 bg-flag-red-soft/45"
              }`}
            >
              <p className="font-body text-[16px] font-semibold leading-snug text-ink">
                {displayGoal}
              </p>
              <div className="mt-4 flex items-start gap-3">
                <span
                  aria-hidden
                  className={`mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
                    g.passed
                      ? "bg-success text-white"
                      : "bg-flag-red text-white"
                  }`}
                >
                  {g.passed ? "✓" : "✗"}
                </span>
                <span className="font-body text-[16px] leading-snug text-ink">
                  <span className="text-ink-muted">You said:</span>{" "}
                  {formatEvidence(g.evidence)}
                </span>
              </div>
              {!g.passed && g.reasoning && (
                <div className="mt-2 ml-9 font-body text-[13px] italic text-ink-muted">
                  {g.reasoning}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function PassBadge({ passed }: { passed: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border-2 px-3 py-1 font-display text-[11px] font-black uppercase tracking-[0.2em] ${
        passed
          ? "border-success bg-success text-white"
          : "border-flag-red bg-flag-red text-white"
      }`}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-white" />
      {passed ? "Passed" : "Not passed"}
    </span>
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

  // Best / toughest turn by accuracy — Azure scores each turn on its own, so
  // no threshold gating, which keeps this meaningful at every level. A single
  // turn has nothing to contrast against, so we show only the best.
  const sortedTurns = [...evalData.turns].sort(
    (a, b) => b.accuracy_score - a.accuracy_score,
  );
  const bestTurn = sortedTurns[0] ?? null;
  const worstTurn =
    sortedTurns.length > 1 ? sortedTurns[sortedTurns.length - 1] : null;

  const hasSubScores =
    schema.showFluency || schema.showCompleteness || schema.showProsody;

  const score = Math.round(agg.accuracy_score);

  return (
    <div className="rounded-[22px] border-[3px] border-ink bg-white px-5 py-5">
      <div className="flex items-center gap-3">
        <span className="font-body text-[11px] font-bold uppercase tracking-[0.32em] text-ink">
          Pronunciation
        </span>
        <span className="h-px flex-1 bg-rule" />
      </div>

      <div className="mt-3 flex items-baseline gap-3">
        <span className="font-display text-[44px] font-black leading-none text-ink">
          {score}
        </span>
        <span className="font-display text-[18px] font-bold text-ink-muted">
          / 100
        </span>
      </div>

      {hasSubScores && (
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 font-body text-[13px] text-ink-soft">
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

      {(bestTurn || worstTurn) && (
        <ul className="mt-4 space-y-2 font-body text-[14px]">
          {bestTurn && (
            <li className="rounded-xl border-2 border-success/35 bg-success-soft/40 px-3 py-2.5">
              <div className="flex items-center justify-between gap-3">
                <span className="font-body text-[10px] font-bold uppercase tracking-[0.22em] text-ink-muted">
                  Best phrase
                </span>
                <span className="shrink-0 font-display text-[14px] font-bold text-ink">
                  {Math.round(bestTurn.accuracy_score)} / 100
                </span>
              </div>
              <p className="mt-1 leading-snug text-ink">“{bestTurn.text}”</p>
            </li>
          )}
          {worstTurn && (
            <li className="rounded-xl border-2 border-flag-gold/55 bg-flag-gold-soft/55 px-3 py-2.5">
              <div className="flex items-center justify-between gap-3">
                <span className="font-body text-[10px] font-bold uppercase tracking-[0.22em] text-ink-muted">
                  Toughest phrase
                </span>
                <span className="shrink-0 font-display text-[14px] font-bold text-ink">
                  {Math.round(worstTurn.accuracy_score)} / 100
                </span>
              </div>
              <p className="mt-1 leading-snug text-ink">“{worstTurn.text}”</p>
            </li>
          )}
        </ul>
      )}

      <p className="mt-4 font-body text-[12px] italic text-ink-muted">
        Remember: Everything above 80 is great!
      </p>
    </div>
  );
}

function SubScore({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="font-body text-[10px] font-bold uppercase tracking-[0.22em] text-ink-muted">
        {label}
      </span>
      <span className="font-display text-[16px] font-bold text-ink">
        {Math.round(value)}
      </span>
    </div>
  );
}

function formatEvidence(evidence: string): React.ReactNode {
  const trimmed = evidence.trim();
  if (!trimmed || trimmed.toLowerCase() === "none") {
    return <span className="italic text-ink-muted">(no attempt)</span>;
  }
  return (
    <span className="text-ink">&ldquo;{trimmed}&rdquo;</span>
  );
}
