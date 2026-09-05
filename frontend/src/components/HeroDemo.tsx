"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { PipecatClient } from "@pipecat-ai/client-js";
import {
  WebSocketTransport,
  ProtobufFrameSerializer,
  WavMediaManager,
} from "@pipecat-ai/websocket-transport";
import { HTTP_BASE, WS_BASE as BASE_WS } from "@/lib/api";
import { loadError } from "./shared/copy";

// PRODUCT-017: honest failure copy for the demo. A rejection on
// `/ws/demo/{id}` closes the socket *before* `accept()` (1008/1013), which
// uvicorn turns into an HTTP 403 handshake rejection — the browser's
// WebSocket API collapses that (and every other handshake failure) into a
// bare onerror/1006 with no code or reason ever exposed to JS. So there is
// no way to tell "server is draining" from "you're rate-limited" from "the
// network is down" by inspecting a failed connection; `GET /demo/status`
// (main.py) exists to answer that question directly, both before connecting
// and again if a connect attempt still fails.
type DemoAvailability = "ok" | "draining" | "busy" | "rate_limited" | "offline";

const DEMO_STATUS_TIMEOUT_MS = 4000;
// `.type-overlay`'s slide-up runs 240ms; focus the sheet's input just after.
const TYPE_SHEET_ENTER_MS = 260;

const DEMO_NOTES = {
  draining: "We're rolling out an update — try again in a minute.",
  busy: "The demo is full right now — every seat is taken. Try again in a minute.",
  rate_limited: "You've used the demo a lot just now — try again in a few minutes.",
  offline: "The demo is offline right now — try again in a moment.",
  // Backend reported "ok" but the connect still failed (e.g. lost the race
  // to a slot, or some other one-off failure) — never blame the mic here.
  connectFailure: "Couldn't start the demo — try again in a moment.",
  // SEC-005 mic pre-flight (2026-09-05): this note used to be an "empirical finding"
  // that a denied/missing mic never rejects connect() or fires onError —
  // that was true of the OLD default media manager (Daily's). It is NOT
  // true of the explicit `WavMediaManager` this file now constructs (see
  // createMediaManager's comment below): with a denied/missing mic and
  // `enableMic` left at its default `true`, WavMediaManager.connect()
  // crashes outright, rejecting client.connect(). The honest tolerance this
  // note describes is application-level now, not a transport fact:
  // `start()`'s own pre-flight probe (probeMicAvailable) decides
  // `enableMic` up front, so this is set directly from that probe result —
  // see the `setNote(DEMO_NOTES.noMic)` call in start(), not in
  // onConnected.
  noMic: "Your mic isn't available right now.",
} as const;

async function classifyDemoAvailability(): Promise<DemoAvailability> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEMO_STATUS_TIMEOUT_MS);
  try {
    const res = await fetch(`${HTTP_BASE}/demo/status`, { signal: controller.signal });
    if (!res.ok) return "offline";
    const data = (await res.json().catch(() => null)) as { status?: string } | null;
    if (
      data?.status === "ok" ||
      data?.status === "draining" ||
      data?.status === "busy" ||
      data?.status === "rate_limited"
    ) {
      return data.status;
    }
    return "offline";
  } catch {
    return "offline";
  } finally {
    clearTimeout(timer);
  }
}

// SEC-005 mic pre-flight: WavMediaManager.disconnect() throws
// "Session ended: please call .begin() first" EVERY time the mic never
// came up — not a rare race. Read against
// node_modules/@pipecat-ai/websocket-transport/dist/index.js:
// WavMediaManager.initialize() always attempts WavRecorder.begin() (i.e.
// getUserMedia) and silently swallows a failure there, leaving
// `this.processor` unset; WavMediaManager.disconnect() then unconditionally
// calls WavRecorder.end() once `_initialized` (set true regardless of
// whether begin() actually succeeded), and end() throws that same error
// whenever `processor` is unset. Passing `enableMic: false` on the
// PipecatClient does NOT fix this — it only skips the separate CONNECT-time
// crash (WavMediaManager.connect() calls _startRecording(), which throws
// the same error, only when `_micEnabled` is true) — because
// initialize()'s begin() call isn't gated on enableMic at all. Left
// unguarded, that throw happens INSIDE WebSocketTransport._disconnect()'s
// own await chain, before `await this._ws?.close()` ever runs — so
// disconnect() never actually closes the socket. For the demo specifically
// that "just" leaves an orphaned per-client pipeline running server-side
// until its own timeout/idle-kill; on the other voice surfaces
// (ConversationView.tsx) the identical bug is what locks a learner out via
// SESS-001. Fix lives here, at the media-manager boundary, so
// WebSocketTransport's own disconnect always reaches `_ws.close()` whether
// or not the mic ever came up.
const BENIGN_TEARDOWN_ERROR = "Session ended: please call .begin() first";
class SafeWavMediaManager extends WavMediaManager {
  async disconnect(): Promise<void> {
    try {
      await super.disconnect();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (!msg.includes(BENIGN_TEARDOWN_ERROR)) {
        console.warn("[HeroDemo] media manager disconnect failed:", e);
      }
    }
  }
}
function createMediaManager(): WavMediaManager {
  return new SafeWavMediaManager(undefined, 16000);
}

// SEC-005 mic pre-flight: know whether the mic will actually work BEFORE constructing
// the client, instead of reacting to WavMediaManager's crash after the
// fact (see createMediaManager above and start() below). The probe's own
// stream is stopped immediately either way — this never leaves a live mic
// track open, and never leaves the real device in a bad state for a
// second, genuine getUserMedia call right after (verified: the transport's
// own later getUserMedia call succeeds normally against a fake input
// device after this probe releases it).
async function probeMicAvailable(): Promise<boolean> {
  if (!navigator.mediaDevices?.getUserMedia) return false;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}

// Front-page voice demo — the Spralingua "welcome" concierge (a `respond`
// lesson, no evaluator). Connects to the hardened public demo socket
// `/ws/demo/{id}`, which forces the welcome lesson + fixed voice server-side
// and applies the SEC-001 guards (Origin allowlist, global + per-IP
// concurrency/rate caps, and a wall-clock session timeout). Each tap mints a
// fresh ephemeral `demo-<uuid>` user id so concurrent visitors never share
// conversation memory or collide in the backend's ACTIVE_TASKS map. The backend
// origin (and ws/wss scheme) comes from NEXT_PUBLIC_API_URL — see lib/api.ts.

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
  // PRODUCT-017 (review finding): every start() takes a fresh attempt number
  // and stop() bumps it, so callbacks belonging to an abandoned attempt (a
  // Stop pressed while the mic dialog or the handshake was still pending)
  // bail out instead of overwriting the idle state — or nulling the NEXT
  // attempt's clientRef from a late onDisconnected.
  const attemptRef = useRef(0);

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
    attemptRef.current += 1;
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
    const attempt = ++attemptRef.current;
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

    // Pre-flight: know why before we try, rather than guessing after a
    // failure with no code/reason attached (see the DEMO_NOTES comment
    // above). Checked before the client is created and before the mic is
    // ever requested.
    const availability = await classifyDemoAvailability();
    if (attemptRef.current !== attempt) return;
    if (availability !== "ok") {
      setNote(DEMO_NOTES[availability]);
      setMode("idle");
      return;
    }

    // SEC-005 mic pre-flight: pre-flight the mic before constructing the client — see
    // probeMicAvailable's and createMediaManager's own comments for why
    // WavMediaManager can't be trusted to degrade gracefully on its own (it
    // crashes connect() outright on a denied/missing mic unless told
    // enableMic: false up front).
    const micAvailable = await probeMicAvailable();
    if (attemptRef.current !== attempt) return;
    if (!micAvailable) setNote(DEMO_NOTES.noMic);

    try {
      const transport = new WebSocketTransport({
        serializer: new ProtobufFrameSerializer(),
        recorderSampleRate: 16000,
        playerSampleRate: 24000,
        // SEC-005 (2026-09-05): explicit media manager. The package's
        // default is Daily's `DailyMediaManager`, which fetches + eval()s a
        // bundle from c.daily.co on every connect for device enumeration
        // and is blocked by the enforced CSP (no 'unsafe-eval', no
        // daily.co origin — see next.config.ts). `WavMediaManager` is the
        // same package's pure Web Audio recorder/player pair; the 16 kHz
        // argument matches `recorderSampleRate`. SEC-005 mic pre-flight: wrapped
        // (createMediaManager, above) so a mic-less teardown still closes
        // the socket — see that function's comment for the mechanism.
        mediaManager: createMediaManager(),
      });
      const client = new PipecatClient({
        transport,
        enableCam: false,
        // SEC-005 mic pre-flight: honors the pre-flight probe above — a denied/missing
        // mic must never be told to record; see probeMicAvailable's and
        // createMediaManager's comments for why.
        enableMic: micAvailable,
        callbacks: {
          onConnected: () => {
            if (attemptRef.current !== attempt) return;
            setMode("live");
            // Empirical finding (PRODUCT-017): this backend never calls
            // Pipecat's RTVI set_bot_ready() (true for every surface, not
            // just the demo), so client.connect()'s own returned promise
            // never resolves on a successful connect -- only a pre-accept
            // rejection settles it (via reject), which is why nothing here
            // can be placed after `await client.connect(...)` and expect to
            // run. onConnected is the one reliable "we're live" signal.
            // SEC-005 mic pre-flight: the no-mic note itself is now set directly from
            // the pre-flight probe above, before this callback ever fires
            // (see DEMO_NOTES.noMic's own comment for why `mediaState` can't
            // be trusted here) — nothing left to do for that case in this
            // callback.
          },
          onDisconnected: () => {
            if (attemptRef.current !== attempt) return;
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
            if (attemptRef.current !== attempt) return;
            // Back to idle first — the re-classification below can take up
            // to DEMO_STATUS_TIMEOUT_MS, and the orb must not sit in
            // "connecting" for that long. The note lands when it's known.
            cleanupAudio();
            clientRef.current = null;
            setMode("idle");
            setSpeakerState("idle");
            void classifyDemoAvailability().then((retry) => {
              if (attemptRef.current !== attempt) return;
              setNote(retry === "ok" ? DEMO_NOTES.connectFailure : DEMO_NOTES[retry]);
            });
          },
        },
      });

      clientRef.current = client;
      const demoUserId = `demo-${crypto.randomUUID()}`;
      demoUserIdRef.current = demoUserId;
      const wsUrl = `${BASE_WS}/ws/demo/${demoUserId}`;
      // NOTE: this never resolves on a successful connect (see the
      // onConnected comment above) -- it only ever settles by throwing, on a
      // genuine pre-accept connection failure. Nothing may be placed after
      // this line and expect to run; that's why the mic check lives in
      // onConnected instead.
      await client.connect({ wsUrl });
    } catch {
      if (attemptRef.current !== attempt) return;
      cleanupAudio();
      clientRef.current = null;
      setMode("idle");
      setSpeakerState("idle");
      const retry = await classifyDemoAvailability();
      if (attemptRef.current !== attempt) return;
      setNote(retry === "ok" ? DEMO_NOTES.connectFailure : DEMO_NOTES[retry]);
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
      setNote(loadError("your message"));
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

  // Focus the input once the sheet has finished sliding in. The sheet is
  // `fixed` at the bottom of the viewport and animates up from
  // `translateY(100%)` over 240ms (`.type-overlay` in globals.css), so at
  // mount time the input sits BELOW the viewport — focusing it there made
  // Chromium reveal the caret by scrolling the document ~600-700px, and
  // once the sheet closed the whole demo card (Stop button included) was
  // above the fold. `preventScroll` alone only guards the focus call's own
  // scroll, not the caret reveal that follows, so the focus waits for the
  // entrance to settle (PRODUCT-017 review finding).
  useEffect(() => {
    if (!typeOpen) return;
    const t = setTimeout(
      () => typeInputRef.current?.focus({ preventScroll: true }),
      TYPE_SHEET_ENTER_MS,
    );
    return () => clearTimeout(t);
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
        className="demo-halo pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-flag-gold/30"
      />
      <div className="relative rounded-[36px] border-[3px] border-line bg-paper-warm p-8 shadow-[0_8px_0_var(--color-line)]">
        {(mode === "connecting" || mode === "live") && (
          <button
            type="button"
            onClick={() => void stop()}
            aria-label="End demo"
            title="End demo"
            className="absolute right-5 top-5 z-10 inline-flex h-9 w-9 items-center justify-center rounded-full border-[3px] border-line bg-card text-ink transition-colors hover:border-red-line hover:bg-flag-red-fill hover:text-on-fill"
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
            {/* AI Act Art. 50(1): in-product disclosure, shown before this
                demo connects — HeroDemo doesn't mount ConversationView, so
                it needs its own copy of the line (see Privacy Policy §8). */}
            <p className="mt-5 max-w-[220px] text-center font-body text-[12px] leading-relaxed text-ink-muted">
              You&apos;ll be speaking with an AI conversation partner — the
              voice is computer-generated.
            </p>
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
                <p className="text-center font-body text-[12px] uppercase tracking-[0.2em] text-ink-muted">
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
            {note === DEMO_NOTES.noMic && mode === "live" && (
              <>
                {" "}
                <button
                  type="button"
                  onClick={() => setTypeOpen(true)}
                  // Don't let the click park focus on this button: the
                  // sheet's input takes focus a moment later, and a focused
                  // in-flow element under a fixed sheet has nothing to gain.
                  onMouseDown={(e) => e.preventDefault()}
                  className="underline underline-offset-2"
                >
                  Type instead
                </button>
              </>
            )}
          </p>
        )}
      </div>
      <audio ref={audioRef} autoPlay />

      {/* Type-a-turn overlay (press /) — slides up from the bottom. Lets you
          send a turn by text when you can't speak; the agent still replies in
          voice. */}
      {mode === "live" && typeOpen && (
        <div
          className="fixed inset-0 z-40 flex items-end justify-center bg-scrim/40 backdrop-blur-[1px]"
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              setTypeOpen(false);
              setDraft("");
            }
          }}
        >
          <div className="type-overlay lift-sheet w-full max-w-[560px] rounded-t-[28px] border-t-[3px] border-x-[3px] border-line bg-elevated px-5 pt-5 pb-6">
            <div className="flex items-center justify-between">
              <span className="font-body text-[10px] font-bold uppercase tracking-[0.32em] text-ink-muted">
                Dev · type a turn
              </span>
              <span className="font-body text-[10px] font-semibold uppercase tracking-[0.18em] text-ink-muted">
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
                className="flex-1 rounded-2xl border-[3px] border-line bg-card px-4 py-3 font-display text-[15px] font-semibold text-ink placeholder:text-ink-muted focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
              />
              <button
                onClick={() => void sendText()}
                disabled={!draft.trim()}
                className="btn-3d rounded-2xl border-[3px] border-line bg-ink-fill px-5 py-3 font-display text-[14px] font-bold uppercase tracking-[0.18em] text-on-fill disabled:cursor-not-allowed disabled:opacity-50"
                style={
                  {
                    ["--shadow-color"]: "var(--color-line)",
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
