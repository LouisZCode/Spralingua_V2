"use client";

// Post-session summary modal. Mounted by ConversationView once the
// WebSocket has closed (whether the agent ended the call or the user
// clicked Finish Lesson). Designed to be reusable — a future Profile
// view should be able to render the same content inline without the
// modal wrapper, just by extracting the inner card.
//
// Future-proofing for the evaluator: when we add LLM-scored feedback,
// it gets a new optional prop (`evaluation`) and renders below the
// message body. Today the shell is intentionally minimal.

export type CompletionStatus = "success" | "info" | "warning";

export interface CompletionData {
  title?: string;
  status?: CompletionStatus;
  message_by_agent?: string;
  message_by_user?: string;
}

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
  open,
  lessonTitle,
  completion,
  endedBy,
  onClose,
}: {
  open: boolean;
  lessonTitle: string;
  completion: CompletionData | null;
  endedBy: "user" | "agent";
  onClose: () => void;
}) {
  if (!open) return null;

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
    // because once the evaluator lands, the modal will host results the
    // user needs to actually read before leaving.
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-2xl rounded-xl bg-slate-800 p-8 shadow-2xl">
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

        <p className="mb-8 whitespace-pre-line text-base text-slate-200">
          {message.trim()}
        </p>

        <button
          onClick={onClose}
          className="w-full rounded-lg bg-green-500 py-3 font-semibold text-slate-900 hover:bg-green-400"
        >
          Back to lessons
        </button>
      </div>
    </div>
  );
}
