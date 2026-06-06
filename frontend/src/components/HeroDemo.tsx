"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { PipecatClient } from "@pipecat-ai/client-js";
import {
  WebSocketTransport,
  ProtobufFrameSerializer,
} from "@pipecat-ai/websocket-transport";

// Front-page voice demo — the Spralingua "welcome" concierge (a `respond`
// lesson, no evaluator). Connects to the hardened public demo socket
// `/ws/demo/{id}`, which forces the welcome lesson + fixed voice server-side
// and applies the SEC-001 guards (Origin allowlist, global + per-IP
// concurrency/rate caps, and a wall-clock session timeout). Each tap mints a
// fresh ephemeral `demo-<uuid>` user id so concurrent visitors never share
// conversation memory or collide in the backend's ACTIVE_TASKS map. Still
// deferred to deployment: serving this over WSS/TLS.
const BASE_WS = "ws://localhost:8765";
const HTTP_BASE = "http://localhost:8765";

// Reveal the bot bubble when its audio finishes playing in the browser
// (bot-started time + clip duration + this margin), not when the text
// arrives — otherwise the user's next transcript jumps ahead of it.
// Mirrors ConversationView.
const REVEAL_SAFETY_MS = 300;

type Mode = "idle" | "connecting" | "live";
type SpeakerState = "idle" | "your_turn" | "agent_thinking" | "agent_speaking";

const STATE_LABEL: Record<SpeakerState, string> = {
  idle: "Speak now",
  your_turn: "Listening…",
  agent_thinking: "Thinking",
  agent_speaking: "Speaking",
};

interface ChatMessage {
  speaker: "you" | "bot";
  text: string;
}

// The voice agent says "lugz.dev/projects/spralingua" out loud (clean to
// pronounce) — map that mention to the canonical write-up URL. Requires a path
// segment so bare brand names like "Babbel.com" are left as plain text.
const LINK_RE = /\b(?:https?:\/\/)?[a-z0-9.-]+\.[a-z]{2,}\/[^\s)]*[^\s).,!?]/gi;

function hrefFor(raw: string): string {
  const lower = raw.toLowerCase();
  if (lower.includes("lugz.dev/projects/spralingua")) {
    return "https://www.lugz.dev/projects/spralingua.html";
  }
  if (lower.startsWith("http://") || lower.startsWith("https://")) return raw;
  return `https://${raw}`;
}

function renderWithLinks(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  LINK_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = LINK_RE.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const raw = m[0];
    nodes.push(
      <a
        key={m.index}
        href={hrefFor(raw)}
        target="_blank"
        rel="noopener noreferrer"
        className="underline underline-offset-2"
      >
        {raw}
      </a>
    );
    last = m.index + raw.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export default function HeroDemo() {
  const [mode, setMode] = useState<Mode>("idle");
  const [speakerState, setSpeakerState] = useState<SpeakerState>("idle");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [note, setNote] = useState<string>("");
  const [draft, setDraft] = useState<string>("");
  const [typeOpen, setTypeOpen] = useState(false);

  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const typeInputRef = useRef<HTMLInputElement | null>(null);
  const demoUserIdRef = useRef<string | null>(null);
  const pendingBotRef = useRef<string | null>(null);
  const botStartedTimeRef = useRef<number | null>(null);
  const revealTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stoppingRef = useRef(false);

  const flushBot = useCallback(() => {
    const t = pendingBotRef.current;
    if (!t) return;
    pendingBotRef.current = null;
    if (revealTimerRef.current) {
      clearTimeout(revealTimerRef.current);
      revealTimerRef.current = null;
    }
    setMessages((prev) => [...prev, { speaker: "bot", text: t }]);
  }, []);

  const cleanupAudio = useCallback(() => {
    const a = audioRef.current;
    if (a?.srcObject) {
      (a.srcObject as MediaStream).getTracks().forEach((t) => t.stop());
      a.srcObject = null;
    }
  }, []);

  const stop = useCallback(async () => {
    if (stoppingRef.current) return;
    stoppingRef.current = true;
    if (revealTimerRef.current) {
      clearTimeout(revealTimerRef.current);
      revealTimerRef.current = null;
    }
    pendingBotRef.current = null;
    const c = clientRef.current;
    clientRef.current = null;
    try {
      if (c) await c.disconnect();
    } catch {
      /* already gone */
    }
    cleanupAudio();
    setMode("idle");
    setSpeakerState("idle");
    setMessages([]);
    stoppingRef.current = false;
  }, [cleanupAudio]);

  const start = useCallback(async () => {
    setMode("connecting");
    setNote("");
    setMessages([]);
    setDraft("");
    setTypeOpen(false);
    pendingBotRef.current = null;
    botStartedTimeRef.current = null;
    if (revealTimerRef.current) {
      clearTimeout(revealTimerRef.current);
      revealTimerRef.current = null;
    }
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
          onConnected: () => setMode("live"),
          onDisconnected: () => {
            // Safety net: if the session ended before the reveal timer fired,
            // show the bot's final line (stop() nulls this on user teardown).
            flushBot();
            cleanupAudio();
            clientRef.current = null;
            setMode("idle");
            setSpeakerState("idle");
          },
          onTrackStarted: (
            track: MediaStreamTrack,
            participant?: { local?: boolean },
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
              setSpeakerState("agent_thinking");
            }
          },
          onBotOutput: (data: { text?: string; audio_duration_ms?: number }) => {
            if (!data.text) return;
            pendingBotRef.current = data.text;
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
              flushBot();
            }, delay);
          },
          onBotStartedSpeaking: () => {
            botStartedTimeRef.current = Date.now();
            setSpeakerState("agent_speaking");
            flushBot();
          },
          onBotStoppedSpeaking: () => {
            setSpeakerState("your_turn");
            if (revealTimerRef.current == null) {
              flushBot();
            }
          },
          onError: () => {
            setNote("Couldn't reach the agent — check your mic and try again.");
            cleanupAudio();
            clientRef.current = null;
            setMode("idle");
            setSpeakerState("idle");
          },
        },
      });

      clientRef.current = client;
      const demoUserId = `demo-${crypto.randomUUID()}`;
      demoUserIdRef.current = demoUserId;
      const wsUrl = `${BASE_WS}/ws/demo/${demoUserId}`;
      await client.connect({ wsUrl });
    } catch {
      setNote("Couldn't connect — is the backend running?");
      cleanupAudio();
      clientRef.current = null;
      setMode("idle");
      setSpeakerState("idle");
    }
  }, [cleanupAudio, flushBot]);

  // Typed-turn fallback (quiet room / no mic): inject text into the live
  // pipeline via /say, targeting this session's ephemeral demo id. The agent
  // still replies with voice — typing only spares you from speaking.
  const sendText = useCallback(async () => {
    const text = draft.trim();
    const userId = demoUserIdRef.current;
    if (!text || mode !== "live" || !userId) return;
    setDraft("");
    setTypeOpen(false);
    setMessages((prev) => [...prev, { speaker: "you", text }]);
    setSpeakerState("agent_thinking");
    try {
      const r = await fetch(`${HTTP_BASE}/say/${userId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) setNote(`Couldn't send your message (${r.status}).`);
    } catch {
      setNote("Couldn't send your message — is the backend running?");
    }
  }, [draft, mode]);

  // Disconnect if the visitor leaves while a demo is live.
  useEffect(() => {
    return () => {
      if (revealTimerRef.current) clearTimeout(revealTimerRef.current);
      void clientRef.current?.disconnect();
      cleanupAudio();
    };
  }, [cleanupAudio]);

  // Keep the transcript pinned to the latest line.
  useEffect(() => {
    const box = transcriptRef.current;
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Press "/" to open the type-a-turn overlay (quiet-room / no-mic fallback);
  // Escape closes it. Only while a demo is live, and skip when focus is already
  // in a field so typing isn't intercepted.
  useEffect(() => {
    if (mode !== "live") return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isTyping =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (e.key === "/" && !isTyping) {
        e.preventDefault();
        setTypeOpen(true);
      } else if (e.key === "Escape" && typeOpen) {
        e.preventDefault();
        setTypeOpen(false);
        setDraft("");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, typeOpen]);

  // Focus the input when the overlay opens.
  useEffect(() => {
    if (typeOpen) typeInputRef.current?.focus();
  }, [typeOpen]);

  const onOrbClick = () => {
    if (mode === "idle") void start();
    else void stop();
  };

  const resting = mode === "idle" && messages.length === 0;
  // Resting orb is the inviting red "your-turn" look (matches the approved
  // hero); connecting is neutral; live mirrors the real speaker state.
  const orbClass =
    mode === "live"
      ? `orb orb-${speakerState.replace("_", "-")}`
      : mode === "connecting"
        ? "orb orb-idle"
        : "orb orb-your-turn";
  const label =
    mode === "connecting"
      ? "Connecting…"
      : mode === "idle"
        ? "Tap to talk"
        : STATE_LABEL[speakerState];

  return (
    <div className="relative mx-auto w-full max-w-[420px]">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-flag-gold/30"
      />
      <div className="relative rounded-[36px] border-[3px] border-ink bg-paper-warm p-8 shadow-[0_8px_0_var(--color-ink)]">
        {(mode === "connecting" || mode === "live") && (
          <button
            type="button"
            onClick={() => void stop()}
            aria-label="End demo"
            title="End demo"
            className="absolute right-5 top-5 z-10 inline-flex h-9 w-9 items-center justify-center rounded-full border-[3px] border-ink bg-white text-ink transition-colors hover:border-flag-red-deep hover:bg-flag-red hover:text-white"
          >
            <svg
              viewBox="0 0 24 24"
              className="h-3.5 w-3.5 fill-current"
              aria-hidden
            >
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        )}
        {resting ? (
          <div className="flex min-h-[440px] flex-col items-center justify-center">
            <OrbButton
              orbClass={orbClass}
              speakerState={speakerState}
              label={label}
              disabled={false}
              onClick={onOrbClick}
            />
          </div>
        ) : (
          <div className="flex min-h-[440px] flex-col items-center gap-4">
            <OrbButton
              orbClass={orbClass}
              speakerState={speakerState}
              label={label}
              disabled={mode === "connecting"}
              onClick={onOrbClick}
            />
            <div
              ref={transcriptRef}
              className="flex max-h-[200px] w-full flex-col gap-2.5 overflow-y-auto pr-1"
              aria-label="Demo transcript"
            >
              {messages.length === 0 ? (
                <p className="text-center font-body text-[12px] uppercase tracking-[0.2em] text-ink-faint">
                  say hello to start
                </p>
              ) : (
                messages.map((m, i) => (
                  <div
                    key={i}
                    className={`flex ${
                      m.speaker === "you" ? "justify-end" : "justify-start"
                    }`}
                  >
                    <div
                      className={`bubble ${
                        m.speaker === "you" ? "bubble-you" : "bubble-them"
                      }`}
                    >
                      {renderWithLinks(m.text)}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
        {note && (
          <p className="mt-3 text-center font-body text-[12px] leading-snug text-flag-red">
            {note}
          </p>
        )}
      </div>
      <audio ref={audioRef} autoPlay />

      {/* Type-a-turn overlay (press /) — slides up from the bottom. Lets you
          send a turn by text when you can't speak; the agent still replies in
          voice. */}
      {mode === "live" && typeOpen && (
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
                  if (e.key === "Enter") void sendText();
                }}
                placeholder="Hi! What is Spralingua?"
                className="flex-1 rounded-2xl border-[3px] border-ink bg-white px-4 py-3 font-display text-[15px] font-semibold text-ink placeholder:text-ink-faint focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
              />
              <button
                onClick={() => void sendText()}
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
    </div>
  );
}

function OrbButton({
  orbClass,
  speakerState,
  label,
  disabled,
  onClick,
}: {
  orbClass: string;
  speakerState: SpeakerState;
  label: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-3">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        className={`${orbClass} cursor-pointer p-0 disabled:cursor-wait`}
      >
        {(speakerState === "your_turn" || speakerState === "agent_speaking") && (
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
          <svg viewBox="0 0 24 24" className="h-12 w-12 fill-current" aria-hidden>
            <path d="M4 5 H20 A2 2 0 0 1 22 7 V15 A2 2 0 0 1 20 17 H10 L6 21 V17 H4 A2 2 0 0 1 2 15 V7 A2 2 0 0 1 4 5 Z" />
          </svg>
        ) : (
          <svg
            viewBox="0 0 24 24"
            className="orb-mic h-12 w-12 fill-current"
            aria-hidden
          >
            <path d="M12 3 A3 3 0 0 1 15 6 V12 A3 3 0 0 1 9 12 V6 A3 3 0 0 1 12 3 Z M6 11 V12 A6 6 0 0 0 18 12 V11 H20 V12 A8 8 0 0 1 13 19.9 V22 H11 V19.9 A8 8 0 0 1 4 12 V11 Z" />
          </svg>
        )}
      </button>
      <p className="font-display text-[13px] font-bold uppercase tracking-[0.28em] text-ink">
        {label}
      </p>
    </div>
  );
}
