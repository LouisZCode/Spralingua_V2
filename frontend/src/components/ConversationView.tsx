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
import TandemDebriefModal from "./TandemDebriefModal";
import { useAuth } from "./auth/AuthContext";
import { HTTP_BASE, WS_BASE as BASE_WS } from "@/lib/api";

// Briefing field values are either a single prose string OR a list of
// short items (renders as bullets). Authors pick per field per lesson.
type BriefingValue = string | string[];

interface LessonMeta {
  title: string;
  briefing: {
    situation: BriefingValue;
    context: BriefingValue;
    goal: BriefingValue;
  };
  completion?: CompletionData | null;
}

interface ChatMessage {
  speaker: "you" | "bot";
  text: string;
}

type SpeakerState =
  | "idle" // connected, nothing happening yet
  | "your_turn" // bot just stopped, waiting for user
  | "agent_thinking" // user transcript received, LLM composing
  | "agent_speaking"; // bot audio playing

const STATE_LABEL: Record<SpeakerState, string> = {
  idle: "Speak now",
  your_turn: "Listening…",
  agent_thinking: "Thinking",
  agent_speaking: "Speaking",
};

// MOBILE-001 P2: iOS Safari only lets an <audio> element play from inside a
// real user gesture. The bot track arrives later, async, in onTrackStarted —
// outside any gesture — so iOS silently rejects that play() and the user never
// hears the agent. We "bless" the element during the "I am ready" click by
// playing this tiny silent clip; once user-activated, the later srcObject
// playback is allowed without a fresh gesture. (Standard empty-WAV unlock.)
const SILENT_AUDIO_DATA_URI =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

export default function ConversationView({
  params,
  onFinish,
}: {
  params: SessionParams;
  onFinish: () => void;
}) {
  // Guaranteed non-null here: VoiceChat only mounts this view once a token is
  // in hand. We still guard before each network call to keep TypeScript happy.
  const { token, user } = useAuth();
  const [phase, setPhase] = useState<"briefing" | "live">("briefing");
  const [meta, setMeta] = useState<LessonMeta | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<string>("Loading...");
  const [draft, setDraft] = useState<string>("");
  const [showSummary, setShowSummary] = useState(false);
  const [showFinishConfirm, setShowFinishConfirm] = useState(false);
  const [endedBy, setEndedBy] = useState<"user" | "agent">("agent");
  const [speakerState, setSpeakerState] = useState<SpeakerState>("idle");
  const [typeOpen, setTypeOpen] = useState(false);
  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const transcriptRef = useRef<HTMLElement | null>(null);
  const typeInputRef = useRef<HTMLInputElement | null>(null);
  const finishedRef = useRef(false);
  const endReasonRef = useRef<"user" | "agent" | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const pendingBotTextRef = useRef<string | null>(null);
  const botStartedTimeRef = useRef<number | null>(null);
  const revealTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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

  // Scroll the transcript box, not the window — scrollIntoView would scroll
  // the whole page and push the header + Finish button off-screen.
  useEffect(() => {
    const box = transcriptRef.current;
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Slash-key opens the type-a-turn overlay; Escape closes it. Only active
  // during the live phase, and we skip when focus is already on an input
  // so users don't get intercepted while typing in the overlay itself.
  useEffect(() => {
    if (phase !== "live") return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isTyping =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (e.key === "/" && !isTyping && !showFinishConfirm) {
        e.preventDefault();
        setTypeOpen(true);
      } else if (e.key === "Escape" && typeOpen) {
        e.preventDefault();
        setTypeOpen(false);
        setDraft("");
      } else if (e.key === "Escape" && showFinishConfirm) {
        e.preventDefault();
        setShowFinishConfirm(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, typeOpen, showFinishConfirm]);

  // When the overlay opens, focus its input.
  useEffect(() => {
    if (typeOpen) typeInputRef.current?.focus();
  }, [typeOpen]);

  // Unmount-only cleanup: browser/mobile back (or any route change) away from
  // a live session skips confirmFinish/onDisconnected entirely, so without
  // this the mic stays hot and the backend keeps the per-client pipeline
  // running until GC. Mirrors HeroDemo's "leave while live" cleanup
  // (frontend/src/components/HeroDemo.tsx). Guard the ref — disconnect() on
  // an already-disconnected client is a safe no-op.
  useEffect(() => {
    return () => {
      if (revealTimerRef.current) clearTimeout(revealTimerRef.current);
      void clientRef.current?.disconnect();
      if (audioRef.current?.srcObject) {
        (audioRef.current.srcObject as MediaStream)
          .getTracks()
          .forEach((t) => t.stop());
        audioRef.current.srcObject = null;
      }
    };
  }, []);

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
    const reason: "user" | "agent" = endReasonRef.current ?? "agent";
    const isTandem = params.lesson === "tandem";
    setShowFinishConfirm(false);
    // User-initiated finish → normally no results; go straight back to the list.
    // The summary (with eval results) is only for lessons the agent completes.
    // Tandem is the exception: its debrief is shown for EVERY end reason, so a
    // user-ended tandem still opens the summary (TANDEM-001 Phase 4).
    if (reason === "user" && !isTandem) {
      onFinish();
      return;
    }
    setEndedBy(reason);
    setShowSummary(true);
  };

  const startCall = async () => {
    if (!token || !user) return;
    // MOBILE-001 P2: prime audio playback INSIDE this click handler (a real
    // user gesture) before any await, so iOS Safari unlocks the element for the
    // bot track that arrives later in onTrackStarted. Must run before the first
    // await or iOS no longer counts it as gesture-driven.
    const audioEl = audioRef.current;
    if (audioEl) {
      audioEl.src = SILENT_AUDIO_DATA_URI;
      audioEl.play().catch((e) => {
        // Non-fatal, but on iOS a rejection here predicts silent bot audio —
        // don't swallow it (the old bug). Surface it instead of hiding it.
        console.warn("Audio unlock failed:", e);
      });
    }
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
            participant?: { local?: boolean },
          ) => {
            if (track.kind === "audio" && participant?.local === false) {
              const stream = new MediaStream([track]);
              if (audioRef.current) {
                const el = audioRef.current;
                // Drop the silent-primer src (MOBILE-001 P2) before attaching
                // the live stream so srcObject is the sole source.
                el.removeAttribute("src");
                el.srcObject = stream;
                el.play().catch((e) => {
                  // Was silently swallowed — a rejection here means the browser
                  // blocked bot audio (iOS autoplay). Make it visible.
                  console.warn("Bot audio playback blocked:", e);
                  setStatus("Audio blocked — tap the screen to enable sound");
                });
              }
            }
          },
          onUserTranscript: (data: { final?: boolean; text?: string }) => {
            if (data.final && data.text) {
              const text = data.text;
              setMessages((prev) => [...prev, { speaker: "you", text }]);
              // STT gave us the final transcript → LLM is composing now.
              setSpeakerState("agent_thinking");
            }
          },
          onBotOutput: (data: { text?: string; audio_duration_ms?: number }) => {
            if (!data.text) return;
            pendingBotTextRef.current = data.text;
            const startedAt = botStartedTimeRef.current;
            const duration = data.audio_duration_ms;
            const target =
              startedAt != null && typeof duration === "number"
                ? startedAt + duration + REVEAL_SAFETY_MS
                : Date.now() + REVEAL_SAFETY_MS;
            const delay = Math.max(0, target - Date.now());
            if (revealTimerRef.current) clearTimeout(revealTimerRef.current);
            revealTimerRef.current = setTimeout(() => {
              revealTimerRef.current = null;
              flushPendingBot();
            }, delay);
          },
          onBotStartedSpeaking: () => {
            botStartedTimeRef.current = Date.now();
            setSpeakerState("agent_speaking");
            flushPendingBot();
          },
          onBotStoppedSpeaking: () => {
            setSpeakerState("your_turn");
            if (revealTimerRef.current == null) {
              flushPendingBot();
            }
          },
          onServerMessage: (data: unknown) => {
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
      // The session JWT rides as a query param — browsers can't set custom
      // headers on a WS handshake. The backend derives identity from the token's
      // subject, so the path id is informational only.
      const wsUrl =
        `${BASE_WS}/ws/${encodeURIComponent(user.id)}` +
        `?voice=${params.voice}` +
        `&lesson=${params.lesson}` +
        (params.topic ? `&topic=${encodeURIComponent(params.topic)}` : "") +
        `&token=${encodeURIComponent(token)}`;
      await client.connect({ wsUrl });
    } catch (e) {
      setStatus(`Connection failed: ${e}`);
    }
  };

  const confirmFinish = async () => {
    setShowFinishConfirm(false);
    endReasonRef.current = "user";
    if (clientRef.current) {
      await clientRef.current.disconnect();
      clientRef.current = null;
    }
    handleFinish();
  };

  const sendText = async () => {
    const text = draft.trim();
    if (!text || phase !== "live" || !token || !user) return;
    setDraft("");
    setTypeOpen(false);
    setMessages((prev) => [...prev, { speaker: "you", text }]);
    setSpeakerState("agent_thinking");
    try {
      const r = await fetch(`${HTTP_BASE}/say/${encodeURIComponent(user.id)}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) setStatus(`Send failed: ${r.status}`);
    } catch (e) {
      setStatus(`Send error: ${e}`);
    }
  };

  return (
    <main className="relative min-h-screen overflow-hidden bg-white text-ink">
      {/* Bauhaus decorations — quieter than SetupView so chat reads cleanly */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-40 -right-40 h-[26rem] w-[26rem] rounded-full bg-flag-gold/25"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-28 -left-24 h-64 w-64 rotate-6 bg-flag-red/70"
        style={{ clipPath: "polygon(0 0, 100% 0, 0 100%)" }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <div className="relative mx-auto flex min-h-screen w-full max-w-[560px] flex-col px-6 py-10">
        {phase === "briefing" && (
          <BriefingPhase
            meta={meta}
            status={status}
            onReady={startCall}
            onBack={onFinish}
          />
        )}
        {phase === "live" && (
          <LivePhase
            title={meta?.title ?? ""}
            messages={messages}
            speakerState={speakerState}
            transcriptRef={transcriptRef}
            onFinish={() => setShowFinishConfirm(true)}
          />
        )}

        <audio ref={audioRef} autoPlay />
      </div>

      {/* Type-a-turn overlay (press /) — slides up from bottom */}
      {phase === "live" && typeOpen && (
        <div
          className="fixed inset-0 z-40 flex items-end justify-center bg-ink/40 backdrop-blur-[1px]"
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              setTypeOpen(false);
              setDraft("");
            }
          }}
        >
          <div className="type-overlay w-full max-w-[560px] rounded-t-[28px] border-t-[3px] border-x-[3px] border-ink bg-paper-warm px-5 pt-5 pb-6 shadow-[0_-12px_30px_rgba(15,15,16,0.18)]">
            <div className="flex items-center justify-between">
              <span className="font-body text-[10px] font-bold uppercase tracking-[0.32em] text-ink-muted">
                Dev · type a turn
              </span>
              <span className="font-body text-[10px] font-semibold uppercase tracking-[0.18em] text-ink-faint">
                Esc to close
              </span>
            </div>
            <div className="mt-3 flex gap-2">
              <input
                ref={typeInputRef}
                type="text"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") sendText();
                }}
                placeholder="Hallo, ich heiße…"
                className="flex-1 rounded-2xl border-[3px] border-ink bg-white px-4 py-3 font-display text-[15px] font-semibold text-ink placeholder:text-ink-faint focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
              />
              <button
                onClick={sendText}
                disabled={!draft.trim()}
                className="btn-3d rounded-2xl border-[3px] border-ink bg-ink px-5 py-3 font-display text-[14px] font-bold uppercase tracking-[0.18em] text-white disabled:cursor-not-allowed disabled:opacity-50"
                style={
                  {
                    ["--shadow-color"]: "var(--color-ink)",
                  } as React.CSSProperties
                }
              >
                Send
              </button>
            </div>
          </div>
        </div>
      )}

      {showSummary &&
        (params.lesson === "tandem" ? (
          <TandemDebriefModal
            lessonTitle={meta?.title ?? ""}
            completion={meta?.completion ?? null}
            endedBy={endedBy}
            sessionId={sessionId}
            onClose={onFinish}
          />
        ) : (
          <SessionSummaryModal
            lessonTitle={meta?.title ?? ""}
            briefingGoal={meta?.briefing?.goal ?? null}
            completion={meta?.completion ?? null}
            endedBy={endedBy}
            sessionId={sessionId}
            onClose={onFinish}
          />
        ))}

      {showFinishConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/55 p-4 backdrop-blur-[2px]">
          <div className="w-full max-w-[420px] rounded-[28px] border-[3px] border-ink bg-paper-warm px-7 py-8 shadow-[0_8px_0_var(--color-ink)]">
            <h2 className="font-display text-[26px] font-black leading-tight text-ink">
              Finish the lesson?
            </h2>
            <div className="mt-7 flex gap-3">
              <button
                onClick={() => setShowFinishConfirm(false)}
                className="btn-3d flex-1 rounded-2xl border-[3px] border-ink bg-white px-5 py-3 font-display text-[14px] font-bold uppercase tracking-[0.16em] text-ink"
                style={
                  { ["--shadow-color"]: "var(--color-ink)" } as React.CSSProperties
                }
              >
                Keep going
              </button>
              <button
                onClick={confirmFinish}
                className="btn-3d flex-1 rounded-2xl border-[3px] border-flag-red-deep bg-flag-red px-5 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-white"
                style={
                  {
                    ["--shadow-color"]: "var(--color-flag-red-deep)",
                  } as React.CSSProperties
                }
              >
                Yes, finish
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

/* ─── Briefing ──────────────────────────────────────────────── */

function BriefingPhase({
  meta,
  status,
  onReady,
  onBack,
}: {
  meta: LessonMeta | null;
  status: string;
  onReady: () => void;
  onBack: () => void;
}) {
  const blocks: Array<{
    key: keyof LessonMeta["briefing"];
    label: string;
    spotlight?: boolean;
  }> = [
    { key: "situation", label: "Situation" },
    { key: "goal", label: "Goal", spotlight: true },
  ];
  const hasContent = (v: BriefingValue | undefined): boolean => {
    if (Array.isArray(v)) return v.some((item) => item.trim().length > 0);
    return (v ?? "").trim().length > 0;
  };
  const filled = meta ? blocks.filter((b) => hasContent(meta.briefing[b.key])) : [];

  return (
    <>
      <header
        className="rise-in text-center"
        style={{ animationDelay: "0ms" }}
      >
        <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
          Briefing · scene preview
        </p>
        <h1 className="mt-2 font-display text-[44px] leading-[1.0] font-black tracking-tight text-ink">
          {meta?.title ?? "Loading…"}
        </h1>
      </header>

      {filled.length > 0 && (
        <div
          className="rise-in mt-10 rounded-[28px] border-[3px] border-ink bg-paper-warm px-7 py-2"
          style={{ animationDelay: "100ms" }}
        >
          {filled.map((b) => (
            <div key={b.key} className="py-7">
              <div className="flex items-center gap-3">
                {b.spotlight ? (
                  <span className="goal-shimmer font-display text-[24px] font-black uppercase tracking-[-0.04em]">
                    {b.label}
                  </span>
                ) : (
                  <span className="font-body text-[15px] font-bold uppercase tracking-[0.32em] text-ink">
                    {b.label}
                  </span>
                )}
              </div>
              <BriefingBody value={meta!.briefing[b.key]} />
            </div>
          ))}
        </div>
      )}

      <button
        onClick={onReady}
        disabled={!meta}
        className="btn-3d rise-in mt-8 flex w-full items-center justify-center gap-3 rounded-[28px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-5 font-display text-[18px] font-black uppercase tracking-[0.18em] text-white disabled:cursor-wait disabled:opacity-60"
        style={
          {
            ["--shadow-color"]: "var(--color-flag-red-deep)",
            animationDelay: "200ms",
          } as React.CSSProperties
        }
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current">
          <path d="M6 4 L20 12 L6 20 Z" />
        </svg>
        I am ready
      </button>

      <div
        className="rise-in mt-4 flex items-center justify-between"
        style={{ animationDelay: "240ms" }}
      >
        <button
          onClick={onBack}
          className="font-body text-[12px] font-semibold uppercase tracking-[0.18em] text-ink-muted hover:text-ink"
        >
          ← back to lessons
        </button>
        <span className="font-body text-[10px] uppercase tracking-[0.2em] text-ink-faint">
          {status}
        </span>
      </div>
    </>
  );
}

function BriefingBody({ value }: { value: BriefingValue }) {
  if (Array.isArray(value)) {
    return (
      <ul className="mt-3 space-y-3">
        {value.map((item, i) => (
          <li key={i} className="flex items-start gap-3">
            <span
              aria-hidden
              className="mt-2.5 inline-block h-2 w-2 shrink-0 rounded-full bg-ink"
            />
            <span className="font-body text-[18px] leading-[1.5] text-ink-soft">
              {item}
            </span>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className="mt-3 whitespace-pre-line font-body text-[18px] leading-[1.5] text-ink-soft">
      {value.trim()}
    </div>
  );
}

/* ─── Live ──────────────────────────────────────────────────── */

function LivePhase({
  title,
  messages,
  speakerState,
  transcriptRef,
  onFinish,
}: {
  title: string;
  messages: ChatMessage[];
  speakerState: SpeakerState;
  transcriptRef: React.RefObject<HTMLElement | null>;
  onFinish: () => void;
}) {
  const orbClass = `orb orb-${speakerState.replace("_", "-")}`;
  return (
    <>
      {/* Header */}
      <header className="rise-in flex items-start justify-between gap-3">
        <div>
          <p className="font-body text-[10px] font-bold uppercase tracking-[0.32em] text-ink-muted">
            In session
          </p>
          <h1 className="mt-1 font-display text-[26px] leading-tight font-black tracking-tight text-ink">
            {title}
          </h1>
        </div>
        <button
          onClick={onFinish}
          aria-label="Finish lesson"
          className="btn-3d shrink-0 rounded-2xl border-[3px] border-ink bg-white px-4 py-2 font-display text-[12px] font-bold uppercase tracking-[0.18em] text-ink hover:bg-flag-red hover:text-white hover:border-flag-red-deep transition-colors"
          style={
            {
              ["--shadow-color"]: "var(--color-ink)",
            } as React.CSSProperties
          }
        >
          ✕ Finish
        </button>
      </header>

      {/* Speaker orb hero — takes all remaining vertical space and centers */}
      <div
        className="rise-in flex flex-1 flex-col items-center justify-center"
        style={{ animationDelay: "80ms" }}
      >
        <div className={orbClass}>
          {(speakerState === "your_turn" ||
            speakerState === "agent_speaking") && (
            <>
              <span aria-hidden className="orb-ripple" />
              <span aria-hidden className="orb-ripple orb-ripple-2" />
            </>
          )}
          {speakerState === "agent_thinking" ? (
            <div className="flex items-center gap-2">
              <span className="orb-dot" />
              <span className="orb-dot" />
              <span className="orb-dot" />
            </div>
          ) : speakerState === "agent_speaking" ? (
            <svg
              viewBox="0 0 24 24"
              className="h-12 w-12 fill-current"
              aria-hidden
            >
              {/* speech bubble */}
              <path d="M4 5 H20 A2 2 0 0 1 22 7 V15 A2 2 0 0 1 20 17 H10 L6 21 V17 H4 A2 2 0 0 1 2 15 V7 A2 2 0 0 1 4 5 Z" />
            </svg>
          ) : (
            /* idle + your-turn: floating mic (soft shadow already de-buttons it) */
            <svg
              viewBox="0 0 24 24"
              className="orb-mic h-12 w-12 fill-current"
              aria-hidden
            >
              {/* mic */}
              <path d="M12 3 A3 3 0 0 1 15 6 V12 A3 3 0 0 1 9 12 V6 A3 3 0 0 1 12 3 Z M6 11 V12 A6 6 0 0 0 18 12 V11 H20 V12 A8 8 0 0 1 13 19.9 V22 H11 V19.9 A8 8 0 0 1 4 12 V11 Z" />
            </svg>
          )}
        </div>
        <p className="mt-5 font-display text-[14px] font-bold uppercase tracking-[0.28em] text-ink">
          {STATE_LABEL[speakerState]}
        </p>
      </div>

      {/* Transcript — caps at ~35vh so the orb stays the hero */}
      <section
        ref={transcriptRef}
        className="rise-in mt-4 flex flex-col gap-3 overflow-y-auto pr-1"
        style={{ animationDelay: "160ms", maxHeight: "35vh" }}
        aria-label="Conversation transcript"
      >
        {messages.map((m, i) => (
          <div
            key={i}
            className={`flex ${
              m.speaker === "you" ? "justify-end" : "justify-start"
            }`}
          >
            <div className={`bubble ${m.speaker === "you" ? "bubble-you" : "bubble-them"}`}>
              {m.text}
            </div>
          </div>
        ))}
      </section>
    </>
  );
}
