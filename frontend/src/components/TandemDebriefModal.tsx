"use client";

// Post-session debrief for the Grammatik-Tandem mode (TANDEM-001 Phase 4).
// Lena's counterpart to SessionSummaryModal: mounted by ConversationView once
// the WebSocket closes — for EVERY end reason, unlike the drill modal, since a
// user-ended tandem still earns its debrief.
//
// Same poll as SessionSummaryModal: the backend debrief runs in
// pipeline/factory.py's finally: block AFTER the WS closes, so `ended_at` is
// briefly NULL. We poll GET /sessions/{id} until it's set (or 90s timeout),
// then render the `error_eval` payload the debrief stored:
//   { patterns[], new_errors[], session_note }
// The session_note is Lena's private memory — deliberately NOT shown here.

import { useEffect, useRef, useState } from "react";
import { HTTP_BASE } from "@/lib/api";
import { useAuth } from "./auth/AuthContext";
import type { CompletionData, CompletionStatus } from "./SessionSummaryModal";
import { diffTokens, MarkedText } from "./shared/feedback";
import { playSound } from "./shared/sound";

interface DebriefPattern {
  pattern_id: string;
  label: string;
  elicited: boolean;
  produced_correctly: boolean;
  evidence: string;
  corrected: string;
  note: string;
  retired: boolean;
}

interface DebriefNewError {
  pattern_id: string;
  label: string;
  sentence: string;
  corrected: string;
  note: string;
}

interface TandemDebriefData {
  session_note?: string;
  patterns: DebriefPattern[];
  new_errors: DebriefNewError[];
}

interface SessionData {
  lesson_id: string;
  ended_at: string | null;
  ended_by: "user" | "agent" | "crash" | null;
  error_eval: TandemDebriefData | null;
}

type EvalStatus = "loading" | "ready" | "timeout" | "no-id";

const POLL_INTERVAL_MS = 1000;
const POLL_TIMEOUT_MS = 90000;
// After this long still on "loading", swap to softer copy — the backend
// debrief can legitimately run past the old 30s ceiling.
const SLOW_LOADING_MS = 20000;

const DEFAULT_TITLE = "Nice chat";
const DEFAULT_STATUS: CompletionStatus = "success";
// TAND-008: fallback copy is partner-parameterized — the YAML `completion:`
// block usually supplies the real messages, these only cover its absence.
const defaultMessageByAgent = (name: string) =>
  `That was a good one — ${name} enjoyed catching up. Bis bald!`;
const defaultMessageByUser = (name: string) =>
  `You wrapped up early. ${name} will be here whenever you want to pick it back up.`;
const messageCrash = (name: string) =>
  `The connection dropped mid-chat. Don't worry — your conversation was saved, and ${name} will be here to pick it back up.`;

const STATUS_STYLES: Record<CompletionStatus, { dot: string; text: string }> = {
  success: { dot: "bg-success", text: "text-success" },
  info: { dot: "bg-ink", text: "text-ink" },
  warning: { dot: "bg-flag-gold-deep", text: "text-flag-gold-deep" },
};

export default function TandemDebriefModal({
  lessonTitle,
  partnerName = "Lena",
  completion,
  endedBy,
  sessionId,
  onClose,
}: {
  lessonTitle: string;
  // TAND-008: which partner's chat this modal wraps (display copy only).
  partnerName?: string;
  completion: CompletionData | null;
  endedBy: "user" | "agent";
  sessionId: string | null;
  onClose: () => void;
}) {
  const { token } = useAuth();
  const [sessionData, setSessionData] = useState<SessionData | null>(null);
  const [evalStatus, setEvalStatus] = useState<EvalStatus>(() =>
    sessionId ? "loading" : "no-id",
  );
  const [slowLoading, setSlowLoading] = useState(false);

  useEffect(() => {
    if (!sessionId || !token) return;

    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let slowTimeoutId: ReturnType<typeof setTimeout> | null = null;

    // Clear the "still working" copy when the effect re-runs (a new session
    // id, or a refreshed token). On mount this is free: the state is already
    // false, so React bails out without a re-render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSlowLoading(false);

    const tick = async () => {
      if (cancelled) return;
      try {
        // GET /sessions/{id} is owner-only (SEC-002) — send the session JWT.
        const r = await fetch(`${HTTP_BASE}/sessions/${sessionId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) return;
        const data = (await r.json()) as SessionData;
        if (cancelled) return;
        if (data.ended_at !== null) {
          setSessionData(data);
          setEvalStatus("ready");
          if (intervalId) clearInterval(intervalId);
          if (timeoutId) clearTimeout(timeoutId);
          if (slowTimeoutId) clearTimeout(slowTimeoutId);
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
      if (slowTimeoutId) clearTimeout(slowTimeoutId);
      setEvalStatus((prev) => (prev === "ready" ? prev : "timeout"));
    }, POLL_TIMEOUT_MS);
    slowTimeoutId = setTimeout(() => {
      if (!cancelled) setSlowLoading(true);
    }, SLOW_LOADING_MS);

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      if (timeoutId) clearTimeout(timeoutId);
      if (slowTimeoutId) clearTimeout(slowTimeoutId);
    };
  }, [sessionId, token]);

  // GAME-001: a retired pattern is the RetiredBanner "Mastered" moment —
  // fires once per load, never on an ordinary (no-retirement) debrief.
  const retiredSoundRef = useRef(false);
  useEffect(() => {
    const hasRetired =
      evalStatus === "ready" &&
      (sessionData?.error_eval?.patterns ?? []).some((p) => p.retired);
    if (hasRetired && !retiredSoundRef.current) {
      retiredSoundRef.current = true;
      playSound("bigwin");
    }
  }, [evalStatus, sessionData]);

  const title = completion?.title ?? DEFAULT_TITLE;
  const status = completion?.status ?? DEFAULT_STATUS;
  // The client can only guess how the session ended (Finish click = "user",
  // every other disconnect lands as "agent"). Once the poll returns, the
  // server's ended_by is authoritative — it distinguishes a backend crash and
  // records a socket drop as "user" — so prefer it over the prop.
  const effectiveEndedBy = sessionData?.ended_by ?? endedBy;
  const message =
    effectiveEndedBy === "crash"
      ? messageCrash(partnerName)
      : effectiveEndedBy === "user"
        ? (completion?.message_by_user ?? defaultMessageByUser(partnerName))
        : (completion?.message_by_agent ?? defaultMessageByAgent(partnerName));
  const styles = STATUS_STYLES[status];

  return (
    // Backdrop locks the chat underneath. No click-through, no Esc, no X icon —
    // single explicit action below (mirrors SessionSummaryModal).
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/55 p-4 backdrop-blur-[2px]">
      <div className="lift-panel max-h-[90vh] w-full max-w-[600px] overflow-y-auto rounded-[28px] border-[3px] border-ink bg-elevated">
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
            <DebriefSection
              status={evalStatus}
              data={sessionData}
              slowLoading={slowLoading}
              partnerName={partnerName}
            />
          </div>

          <button
            onClick={onClose}
            className="btn-3d mt-8 flex w-full items-center justify-center gap-3 rounded-[24px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-4 font-display text-[16px] font-black uppercase tracking-[0.18em] text-on-fill"
            style={
              {
                ["--shadow-color"]: "var(--color-flag-red-deep)",
              } as React.CSSProperties
            }
          >
            ← Back to modes
          </button>
        </div>
      </div>
    </div>
  );
}

function DebriefSection({
  status,
  data,
  slowLoading,
  partnerName,
}: {
  status: EvalStatus;
  data: SessionData | null;
  slowLoading: boolean;
  partnerName: string;
}) {
  if (status === "loading") {
    return (
      <div className="rounded-[22px] border-[3px] border-ink bg-card px-5 py-4">
        <div className="flex items-center gap-3 font-body text-[14px] text-ink-soft">
          <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink-faint border-t-ink" />
          {slowLoading
            ? "Still looking — this can take a minute…"
            : `${partnerName} is looking over your chat…`}
        </div>
      </div>
    );
  }

  if (status === "no-id" || status === "timeout") {
    return (
      <div className="rounded-[22px] border-[3px] border-ink bg-card px-5 py-4 font-body text-[14px] text-ink-muted">
        Notes unavailable right now.
      </div>
    );
  }

  if (!data) return null;

  const debrief = data.error_eval;
  // No debrief payload (it didn't run, or failed non-fatally) → keep it warm and
  // say nothing clinical.
  if (!debrief) {
    return (
      <div className="rounded-[22px] border-[3px] border-ink bg-card px-5 py-4 font-body text-[14px] text-ink-soft">
        {partnerName} enjoyed the conversation — no notes this time.
      </div>
    );
  }

  const patterns = debrief.patterns ?? [];
  const newErrors = debrief.new_errors ?? [];
  const retired = patterns.filter((p) => p.retired);
  // Elicited-but-not-yet-retired patterns get the ✓/✗ breakdown. Retired ones
  // graduate to the celebration banner instead, so they aren't listed twice.
  const practiced = patterns.filter((p) => p.elicited && !p.retired);

  const nothing =
    retired.length === 0 && practiced.length === 0 && newErrors.length === 0;

  if (nothing) {
    return (
      <div className="rounded-[22px] border-[3px] border-ink bg-card px-5 py-4 font-body text-[15px] text-ink-soft">
        A clean, easy chat — nothing to flag today. Keep it up. 🎉
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {retired.length > 0 && (
        <RetiredBanner patterns={retired} partnerName={partnerName} />
      )}
      {practiced.length > 0 && <PracticedBlock patterns={practiced} />}
      {newErrors.length > 0 && <NewErrorsBlock errors={newErrors} />}
    </div>
  );
}

function RetiredBanner({
  patterns,
  partnerName,
}: {
  patterns: DebriefPattern[];
  partnerName: string;
}) {
  return (
    <div className="rounded-[22px] border-[3px] border-success bg-success-soft/50 px-5 py-5">
      <div className="flex items-center gap-2.5">
        <span aria-hidden className="text-[22px]">
          🎉
        </span>
        <span className="font-display text-[15px] font-black uppercase tracking-[0.14em] text-success">
          Mastered
        </span>
      </div>
      <p className="mt-2 font-body text-[15px] leading-snug text-ink">
        You used {patterns.length === 1 ? "this" : "these"} correctly on your own,
        twice running — {partnerName} won&apos;t need to nudge it anymore:
      </p>
      <ul className="mt-3 flex flex-wrap gap-2">
        {patterns.map((p) => (
          <li
            key={p.pattern_id}
            className="rounded-full border-2 border-success bg-card px-3.5 py-1.5 font-body text-[13px] font-semibold text-ink"
          >
            {p.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

function PracticedBlock({ patterns }: { patterns: DebriefPattern[] }) {
  return (
    <div className="rounded-[22px] border-[3px] border-ink bg-card px-5 py-5">
      <div className="flex items-center gap-3">
        <span className="font-body text-[11px] font-bold uppercase tracking-[0.32em] text-ink">
          Grammar you practiced
        </span>
        <span className="h-px flex-1 bg-rule" />
      </div>

      <ul className="mt-4 space-y-4">
        {patterns.map((p) => {
          const d = p.corrected ? diffTokens(p.evidence, p.corrected, { caseInsensitive: true }) : null;
          return (
            <li
              key={p.pattern_id}
              className={`rounded-2xl border-2 px-4 py-3.5 ${
                p.produced_correctly
                  ? "border-success/35 bg-success-soft/40"
                  : "border-flag-gold/55 bg-flag-gold-soft/50"
              }`}
            >
              <div className="flex items-start gap-3">
                <span
                  aria-hidden
                  className={`mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-on-fill ${
                    p.produced_correctly ? "bg-success" : "bg-flag-gold-deep"
                  }`}
                >
                  {p.produced_correctly ? "✓" : "↻"}
                </span>
                <div className="min-w-0">
                  <p className="font-body text-[15px] font-semibold leading-snug text-ink">
                    {p.label}
                  </p>
                  {p.produced_correctly ? (
                    <p className="mt-1 font-body text-[14px] leading-snug text-ink-soft">
                      You got it right — “{p.evidence}”
                    </p>
                  ) : (
                    <p className="mt-1 font-body text-[14px] leading-snug text-ink-soft">
                      Came up again: “{d ? <MarkedText tokens={d.attempt} mark="red" /> : p.evidence}”
                      {d && (
                        <>
                          {" → "}
                          <span className="font-semibold text-ink">
                            “<MarkedText tokens={d.corrected} mark="green" />”
                          </span>
                        </>
                      )}
                    </p>
                  )}
                  {!p.produced_correctly && p.note && (
                    <p className="mt-1 font-body text-[12px] italic text-ink-muted">
                      {p.note}
                    </p>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function NewErrorsBlock({ errors }: { errors: DebriefNewError[] }) {
  return (
    <div className="rounded-[22px] border-[3px] border-ink bg-card px-5 py-5">
      <div className="flex items-center gap-3">
        <span className="font-body text-[11px] font-bold uppercase tracking-[0.32em] text-ink">
          New to work on
        </span>
        <span className="h-px flex-1 bg-rule" />
      </div>

      <ul className="mt-4 space-y-4">
        {errors.map((e, i) => {
          const d = e.corrected ? diffTokens(e.sentence, e.corrected, { caseInsensitive: true }) : null;
          return (
            <li
              key={`${e.pattern_id}-${i}`}
              className="rounded-2xl border-2 border-flag-red/40 bg-flag-red-soft/40 px-4 py-3.5"
            >
              <p className="font-body text-[10px] font-bold uppercase tracking-[0.22em] text-ink-muted">
                {e.label}
              </p>
              <p className="mt-1.5 font-body text-[14px] leading-snug text-ink-soft">
                “{d ? <MarkedText tokens={d.attempt} mark="red" /> : e.sentence}”
                {d && (
                  <>
                    {" → "}
                    <span className="font-semibold text-ink">
                      “<MarkedText tokens={d.corrected} mark="green" />”
                    </span>
                  </>
                )}
              </p>
              {e.note && (
                <p className="mt-1 font-body text-[12px] italic text-ink-muted">
                  {e.note}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
