"use client";

import { useEffect, useRef, useState } from "react";
import { PipecatClient } from "@pipecat-ai/client-js";
import {
  WebSocketTransport,
  ProtobufFrameSerializer,
} from "@pipecat-ai/websocket-transport";
import type { SessionParams } from "./SetupView";
import SessionSummaryModal, {
  type CompletionData,
} from "./SessionSummaryModal";

// Hardcoded user id (matches `agents/fake_profiles.py::0001`). Auth-issued
// ids replace this once user accounts land.
const USER_ID = "0001";
const BASE_WS = "ws://localhost:8765";
const HTTP_BASE = "http://localhost:8765";

interface LessonMeta {
  title: string;
  briefing: { situation: string; context: string; goal: string };
  completion?: CompletionData | null;
}

interface ChatMessage {
  speaker: "you" | "bot";
  text: string;
}

export default function ConversationView({
  params,
  onFinish,
}: {
  params: SessionParams;
  onFinish: () => void;
}) {
  const [phase, setPhase] = useState<"briefing" | "live">("briefing");
  const [meta, setMeta] = useState<LessonMeta | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<string>("Loading...");
  const [draft, setDraft] = useState<string>("");
  const [showSummary, setShowSummary] = useState(false);
  const [endedBy, setEndedBy] = useState<"user" | "agent">("agent");
  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const finishedRef = useRef(false);
  // Tracks who ended the session. `finishLesson()` sets this to "user"
  // before disconnecting; otherwise `handleFinish` defaults it to "agent"
  // (covers goodbye / max_exchanges / network drop). Read by the summary
  // modal to pick which message to show.
  const endReasonRef = useRef<"user" | "agent" | null>(null);
  // Backend pushes a {type: "session_started", session_id, lesson_id} RTVI
  // server-message right after on_client_ready fires. We stash the id in
  // state (not a ref) so it's safe to read during render when handing it
  // to <SessionSummaryModal>. Stays null until the message arrives — modal
  // treats null as "results unavailable" and skips polling.
  const [sessionId, setSessionId] = useState<string | null>(null);
  // Bot text bubble is held here until audio playback finishes in the browser.
  // The backend now pushes the RTVIBotOutputMessage only after server-side TTS is
  // done, and attaches `audio_duration_ms`. We use `botStartedTime + duration +
  // safety margin` to schedule the bubble reveal so it lands after the user has
  // actually heard the line. `onBotStoppedSpeaking` is the fallback flush when
  // duration is missing (older backend, or skipped turn).
  const pendingBotTextRef = useRef<string | null>(null);
  const botStartedTimeRef = useRef<number | null>(null);
  const revealTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Browser audio queue fudge: TTSDurationTracker's audio_duration_ms is the
  // server-side end of audio; client buffer plays for a bit longer.
  const REVEAL_SAFETY_MS = 300;

  // Fetch briefing copy when the view mounts.
  useEffect(() => {
    fetch(`${HTTP_BASE}/lessons/${params.lesson}`)
      .then((r) => r.json())
      .then((data: LessonMeta) => {
        setMeta(data);
        setStatus("Ready");
      })
      .catch((e) => setStatus(`Failed to load: ${e}`));
  }, [params.lesson]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const flushPendingBot = () => {
    const text = pendingBotTextRef.current;
    if (!text) return;
    pendingBotTextRef.current = null;
    if (revealTimerRef.current) {
      clearTimeout(revealTimerRef.current);
      revealTimerRef.current = null;
    }
    setMessages((prev) => [...prev, { speaker: "bot", text }]);
  };

  // Single funnel for any disconnect path (manual button, system end, error).
  // Re-entry guard so the audio cleanup + summary trigger fire exactly once.
  // We no longer call `onFinish()` from here — the SessionSummaryModal owns
  // the transition back to the setup view (its "Back to lessons" button
  // calls onFinish). This gives the user a moment to read the per-lesson
  // outcome and leaves a slot for the future evaluator widget.
  const handleFinish = () => {
    if (finishedRef.current) return;
    finishedRef.current = true;
    if (revealTimerRef.current) {
      clearTimeout(revealTimerRef.current);
      revealTimerRef.current = null;
    }
    if (audioRef.current?.srcObject) {
      (audioRef.current.srcObject as MediaStream)
        .getTracks()
        .forEach((t) => t.stop());
      audioRef.current.srcObject = null;
    }
    // Mirror the ref into state so the modal re-renders with the right
    // message. Reading `.current` during render trips react-hooks/refs.
    const reason: "user" | "agent" = endReasonRef.current ?? "agent";
    setEndedBy(reason);
    setShowSummary(true);
  };

  const startCall = async () => {
    setPhase("live");
    setStatus("Connecting...");
    try {
      const transport = new WebSocketTransport({
        serializer: new ProtobufFrameSerializer(),
        recorderSampleRate: 16000,
        playerSampleRate: 24000,
      });

      const client = new PipecatClient({
        transport,
        enableCam: false,
        enableMic: true,
        callbacks: {
          onConnected: () => setStatus("Connected"),
          onDisconnected: () => {
            setStatus("Disconnected");
            handleFinish();
          },
          onTransportStateChanged: (state: string) => setStatus(state),
          onTrackStarted: (
            track: MediaStreamTrack,
            participant?: { local?: boolean }
          ) => {
            if (track.kind === "audio" && participant?.local === false) {
              const stream = new MediaStream([track]);
              if (audioRef.current) {
                audioRef.current.srcObject = stream;
                audioRef.current.play().catch(() => {});
              }
            }
          },
          onUserTranscript: (data: { final?: boolean; text?: string }) => {
            if (data.final && data.text) {
              const text = data.text;
              setMessages((prev) => [...prev, { speaker: "you", text }]);
            }
          },
          onBotOutput: (data: { text?: string; audio_duration_ms?: number }) => {
            if (!data.text) return;
            pendingBotTextRef.current = data.text;

            // Schedule the bubble reveal. Backend now pushes this message at the
            // server-side end of audio (TTSDurationTracker on BotStoppedSpeaking),
            // so we always reveal soon — the only choice is precise vs. fallback.
            const startedAt = botStartedTimeRef.current;
            const duration = data.audio_duration_ms;
            const target =
              startedAt != null && typeof duration === "number"
                ? startedAt + duration + REVEAL_SAFETY_MS
                : Date.now() + REVEAL_SAFETY_MS; // fallback: small client-buffer margin
            const delay = Math.max(0, target - Date.now());
            if (revealTimerRef.current) clearTimeout(revealTimerRef.current);
            revealTimerRef.current = setTimeout(() => {
              revealTimerRef.current = null;
              flushPendingBot();
            }, delay);
          },
          onBotStartedSpeaking: () => {
            botStartedTimeRef.current = Date.now();
            // Safety net: if a previous turn's text never got flushed, surface
            // it now before the new turn so it isn't lost silently.
            flushPendingBot();
          },
          onBotStoppedSpeaking: () => {
            // Final safety net. Reveal is normally driven by the setTimeout in
            // onBotOutput; only flush here if a stale pending text somehow exists
            // without a scheduled timer (e.g., bot-output dropped on the wire).
            if (revealTimerRef.current == null) {
              flushPendingBot();
            }
          },
          onServerMessage: (data: unknown) => {
            // Backend's "session_started" handshake — see EVAL-UI-001.
            // Any other server-message types are ignored here; add a switch
            // if/when we introduce more server→client events.
            if (
              data &&
              typeof data === "object" &&
              (data as { type?: unknown }).type === "session_started"
            ) {
              const sid = (data as { session_id?: unknown }).session_id;
              if (typeof sid === "string") {
                setSessionId(sid);
              }
            }
          },
          onError: (err: unknown) =>
            setStatus(`Error: ${JSON.stringify(err)}`),
        },
      });

      clientRef.current = client;
      const wsUrl =
        `${BASE_WS}/ws/${USER_ID}` +
        `?level=${params.level}` +
        `&situation=${params.situation}` +
        `&voice=${params.voice}` +
        `&lesson=${params.lesson}`;
      await client.connect({ wsUrl });
    } catch (e) {
      setStatus(`Connection failed: ${e}`);
    }
  };

  const finishLesson = async () => {
    // Mark this disconnect as user-initiated BEFORE we ask the client
    // to disconnect — the resulting `onDisconnected` may race ahead of
    // the `await` here, and we need handleFinish to see the right ref.
    endReasonRef.current = "user";
    if (clientRef.current) {
      await clientRef.current.disconnect();
      clientRef.current = null;
    }
    // onDisconnected may also fire and call handleFinish — the ref guards it.
    handleFinish();
  };

  // Type-a-turn (kept as a dev/fallback affordance): POSTs to /say/{user_id};
  // the backend injects an LLMContextFrame so the agent reacts as if STT had
  // produced the text. TTS / chat updates flow back the normal way.
  const sendText = async () => {
    const text = draft.trim();
    if (!text || phase !== "live") return;
    setDraft("");
    setMessages((prev) => [...prev, { speaker: "you", text }]);
    try {
      const r = await fetch(`${HTTP_BASE}/say/${USER_ID}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) setStatus(`Send failed: ${r.status}`);
    } catch (e) {
      setStatus(`Send error: ${e}`);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-full max-w-md rounded-xl bg-slate-800 p-8">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h1 className="text-lg font-bold leading-tight">
            {meta?.title ?? "Loading..."}
          </h1>
          {phase === "live" && (
            <button
              onClick={finishLesson}
              className="shrink-0 rounded-lg bg-red-500 px-3 py-1 text-xs font-semibold text-white hover:bg-red-400"
            >
              Finish Lesson
            </button>
          )}
        </div>

        <p className="mb-6 text-center text-xs text-slate-400">
          Status: <span className="font-semibold text-slate-200">{status}</span>
        </p>

        {phase === "briefing" && meta && (() => {
          // Only render a briefing block if its content is non-empty.
          // This lets minimal lessons (e.g. internal test tools) populate
          // just `situation` and skip context/goal without leaving empty
          // labeled boxes behind.
          const blocks: Array<{ key: keyof typeof meta.briefing; label: string }> = [
            { key: "situation", label: "Situation" },
            { key: "context", label: "Context" },
            { key: "goal", label: "Goal" },
          ];
          const filled = blocks.filter(
            (b) => (meta.briefing[b.key] ?? "").trim().length > 0
          );
          return (
            <>
              {filled.map((b, i) => (
                <div
                  key={b.key}
                  className={`${i === filled.length - 1 ? "mb-6" : "mb-3"} rounded-lg bg-slate-900 p-4`}
                >
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">
                    {b.label}
                  </div>
                  <div className="whitespace-pre-line text-sm text-slate-200">
                    {meta.briefing[b.key].trim()}
                  </div>
                </div>
              ))}
              <button
                onClick={startCall}
                className="w-full rounded-lg bg-green-500 py-3 font-semibold text-slate-900 hover:bg-green-400"
              >
                I am ready
              </button>
            </>
          );
        })()}

        {phase === "live" && (
          <>
            <div className="h-80 overflow-y-auto rounded-lg bg-slate-900 p-4 text-sm">
              {messages.length === 0 && (
                <div className="text-center text-slate-500">
                  Listening — say something to start.
                </div>
              )}
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={
                    m.speaker === "you"
                      ? "mb-2 flex justify-end"
                      : "mb-2 flex justify-start"
                  }
                >
                  <div
                    className={
                      m.speaker === "you"
                        ? "max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-white"
                        : "max-w-[80%] rounded-lg bg-slate-700 px-3 py-2 text-slate-100"
                    }
                  >
                    {m.text}
                  </div>
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>

            <div className="mt-3 flex gap-2">
              <input
                type="text"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") sendText();
                }}
                placeholder="Type a turn instead of speaking..."
                className="flex-1 rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500"
              />
              <button
                onClick={sendText}
                disabled={!draft.trim()}
                className="rounded-lg bg-blue-500 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </>
        )}

        <audio ref={audioRef} autoPlay />
      </div>
      {showSummary && (
        <SessionSummaryModal
          lessonTitle={meta?.title ?? ""}
          completion={meta?.completion ?? null}
          endedBy={endedBy}
          sessionId={sessionId}
          onClose={onFinish}
        />
      )}
    </div>
  );
}
