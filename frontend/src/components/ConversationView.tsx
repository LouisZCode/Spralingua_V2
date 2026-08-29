"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
import GermanWay from "./shared/GermanWay";
import Glossable from "./shared/Glossable";
import { useRecorder } from "./shared/recorder";
import { TANDEM_LESSONS, partnerByLesson } from "./shared/tandem";
import { TEACHER_LESSON } from "./shared/teacher";
import type { GlossInfo } from "./satzschmiede/api";

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

// AGENT-001: Natural mode's labels ("Speak now" / "Listening…") describe a mic
// that's always hot — wrong in practice mode, where nothing is listening
// until the learner taps Record. Same four states, honest copy.
const PRACTICE_STATE_LABEL: Record<SpeakerState, string> = {
  idle: "Ready when you are",
  your_turn: "Your turn",
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
  onBack,
  onGloss,
  onAdd,
  practiceMode,
  typedInput,
  agentOpens,
  skipBriefing,
  onExerciseRequest,
  onBotReply,
  onSessionEnded,
  exerciseSlot,
}: {
  params: SessionParams;
  onFinish: () => void;
  // Pre-call "back to lessons" on the briefing screen. Defaults to onFinish;
  // tandem passes its own so backing out returns to the topic picker while a
  // finished session's "Back to modes" leaves for /practice (BUG-008).
  onBack?: () => void;
  // UI-009: word-gloss popover wiring for the partner's chat bubbles —
  // optional, absent renders plain text exactly as before. Only TandemChat
  // wires these; the /learn lesson flow (VoiceChat) passes nothing.
  onGloss?: (word: string, context: string) => Promise<GlossInfo>;
  onAdd?: (lemma: string) => Promise<{ glossRemaining?: number } | void>;
  // TAND-003: Practice input mode — tap record / speak at your own pace / tap
  // stop, instead of the streaming-VAD Natural mode. Undefined/false (every
  // existing caller) is Natural and stays byte-identical: the mic connects
  // exactly as before and none of the practice branches below ever render.
  practiceMode?: boolean;
  // AGENT-001: surface the type-a-turn overlay as a first-class button (the
  // teacher's text-chat channel). Absent/false keeps the overlay dev-only
  // (press /).
  typedInput?: boolean;
  // AGENT-001: lessons with a YAML `kickoff` — the agent speaks first, ~1.5-4s
  // after connect (pipeline/factory.py::_kickoff_turn). The session opens on
  // the agent's turn, not the learner's, so Record must stay locked (and the
  // orb must not read "ready") until that opening line finishes playing.
  // Absent/false (every existing caller but Clara) is byte-identical.
  agentOpens?: boolean;
  // Clara's entry flow skips the briefing/"scene preview" screen entirely —
  // the topic screen (TeacherTopicScreen) IS her briefing now. When true,
  // this view mounts straight into the live phase and auto-fires the same
  // start/connect handler the briefing's "I am ready" button calls (see the
  // mount effect right after startCall's definition). Absent/false (every
  // existing caller but Clara) is byte-identical: phase still starts
  // "briefing" and nothing auto-connects.
  skipBriefing?: boolean;
  // AGENT-00X: Clara's interactive-exercise loop, teacher lessons only.
  // Fires once per `[[ÜBUNG: <id>]]`-terminated reply, but not until that
  // reply's bubble has actually revealed (see flushPendingBot) — the card
  // must never appear while she's still mid-sentence. Optional/absent for
  // VoiceChat and TandemChat, which never pass it — zero behavior change
  // there.
  onExerciseRequest?: (patternId: string) => void;
  // AGENT-00X: fires every time ANY bot reply lands visually, marker or not
  // — same reveal point as onExerciseRequest, just unconditional. The
  // teacher room uses this to auto-dismiss a graded exercise card once
  // Clara's follow-up to the ÜBUNGSERGEBNIS report arrives (TeacherChat.tsx).
  // Optional/absent for VoiceChat and TandemChat.
  onBotReply?: () => void;
  // AGENT-00X: fires exactly once, the instant a session winds down (WS
  // closed or Finish confirmed) — before the summary/debrief modal even
  // renders. The teacher room uses this to drop a still-open exercise card
  // without sending anything (see TeacherChat.tsx). Optional/absent for
  // VoiceChat and TandemChat.
  onSessionEnded?: () => void;
  // CLARA-13: the exercise slot itself — an opaque node the caller builds
  // (TeacherChat mounts one of the five real drill trainers, wrapped in its
  // own Skip row). This component doesn't know or care what's inside; it
  // only decides WHEN to reveal it (the bot-reply reveal moment above plus a
  // fixed pause — see that file's EXERCISE_REVEAL_DELAY_MS) and renders it
  // inline in the chat flow after the last bubble, once `exerciseSlot` goes
  // non-null. That same non-null check is the ONLY gate for every
  // single-focus behavior below (hiding Record/Type, ignoring the "/"
  // shortcut) — VoiceChat and TandemChat never pass this prop, so none of it
  // is reachable there.
  exerciseSlot?: React.ReactNode;
}) {
  // Guaranteed non-null here: VoiceChat only mounts this view once a token is
  // in hand. We still guard before each network call to keep TypeScript happy.
  const { token, user } = useAuth();
  // skipBriefing (Clara): start straight in "live" — see startCall's mount
  // effect below, which fires the connect handler this initial value would
  // otherwise wait for a briefing-screen click to trigger.
  const [phase, setPhase] = useState<"briefing" | "live">(
    skipBriefing ? "live" : "briefing"
  );
  const [meta, setMeta] = useState<LessonMeta | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<string>("Loading...");
  const [draft, setDraft] = useState<string>("");
  const [showSummary, setShowSummary] = useState(false);
  const [showFinishConfirm, setShowFinishConfirm] = useState(false);
  const [endedBy, setEndedBy] = useState<"user" | "agent">("agent");
  const [speakerState, setSpeakerState] = useState<SpeakerState>("idle");
  const [typeOpen, setTypeOpen] = useState(false);
  // CLARA-13: single focus while Clara's exercise slot is up — this is the
  // one flag every focus-mode branch below checks, and it's derived purely
  // from the `exerciseSlot` prop's presence (never a lesson-type guess), so
  // it's always false for VoiceChat/TandemChat.
  const exerciseActive = exerciseSlot != null;
  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const transcriptRef = useRef<HTMLElement | null>(null);
  const typeInputRef = useRef<HTMLInputElement | null>(null);
  const finishedRef = useRef(false);
  const endReasonRef = useRef<"user" | "agent" | null>(null);
  // BUG-005: guards against two fast Enter presses firing sendText twice
  // before React clears `draft`.
  const sendingRef = useRef(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const pendingBotTextRef = useRef<string | null>(null);
  // AGENT-00X: set by onServerMessage when an "exercise_request" RTVI
  // message arrives (always strictly before this turn's bot-output message —
  // see agents/pipecat_wrapper.py). Consumed — and cleared — by
  // flushPendingBot once this turn's reply actually reveals.
  const pendingExercisePatternRef = useRef<string | null>(null);
  const botStartedTimeRef = useRef<number | null>(null);
  const revealTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const REVEAL_SAFETY_MS = 300;
  // BUG-009: Railway's edge proxy silently idle-kills a WebSocket after ~5min
  // of no traffic, and the browser never receives a close frame (half-open
  // socket) — onDisconnected simply never fires, so the UI is stuck on
  // LISTENING. The backend counters this with a ~25s RTVI heartbeat
  // (pipeline/factory.py::_rtvi_heartbeat); this ref tracks the last time ANY
  // real traffic arrived (heartbeat OR an actual turn), so the liveness
  // watchdog below can tell a merely-quiet conversation from a dead socket.
  const lastActivityRef = useRef<number>(Date.now());
  // skipBriefing (Clara): guards the mount effect below so it fires
  // startCall exactly once, not on every re-render.
  const autoStartedRef = useRef(false);

  // Fetch briefing copy when the view mounts. Aborted on unmount so a fast
  // unmount can't setState on an unmounted component.
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${HTTP_BASE}/lessons/${params.lesson}`, { signal: controller.signal })
      .then((r) => r.json())
      .then((data: LessonMeta) => {
        setMeta(data);
        setStatus("Ready");
      })
      .catch((e) => {
        if ((e as Error)?.name === "AbortError") return;
        setStatus(`Failed to load: ${e}`);
      });
    return () => controller.abort();
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
  // AGENT-00X: also inert while an exercise card is up — single focus means
  // the shortcut can't open a second interaction alongside it.
  useEffect(() => {
    if (phase !== "live") return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isTyping =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (e.key === "/" && !isTyping && !showFinishConfirm && !exerciseActive) {
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
  }, [phase, typeOpen, showFinishConfirm, exerciseActive]);

  // When the overlay opens, focus its input.
  useEffect(() => {
    if (typeOpen) typeInputRef.current?.focus();
  }, [typeOpen]);

  // AGENT-00X: single focus — an exercise reveal wins over an already-open
  // type overlay (the edge case where the learner opened it during the ~2s
  // reveal pause between her reply landing and the card actually appearing).
  useEffect(() => {
    if (exerciseActive && typeOpen) {
      setTypeOpen(false);
      setDraft("");
    }
  }, [exerciseActive, typeOpen]);

  // Unmount-only cleanup: browser/mobile back (or any route change) away from
  // a live session skips confirmFinish/onDisconnected entirely, so without
  // this the mic stays hot and the backend keeps the per-client pipeline
  // running until GC. Mirrors HeroDemo's "leave while live" cleanup
  // (frontend/src/components/HeroDemo.tsx). Guard the ref — disconnect() on
  // an already-disconnected client is a safe no-op.
  useEffect(() => {
    // Captured here rather than read in the cleanup (react-hooks/exhaustive-deps):
    // <audio ref={audioRef}> renders unconditionally, so the ELEMENT is stable
    // for the component's whole life — only its .srcObject is swapped, in
    // onTrackStarted. Same element either way; this just satisfies the rule.
    const audioEl = audioRef.current;
    return () => {
      if (revealTimerRef.current) clearTimeout(revealTimerRef.current);
      void clientRef.current?.disconnect();
      // Stops the bot's incoming audio track (the mic goes down with
      // disconnect() above). Only ever one stream — each onTrackStarted
      // replaces srcObject outright.
      if (audioEl?.srcObject) {
        (audioEl.srcObject as MediaStream).getTracks().forEach((t) => t.stop());
        audioEl.srcObject = null;
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
    // AGENT-00X: this is the single place a turn's reply becomes visible to
    // the learner (the timer above, or onBotStoppedSpeaking's fallback,
    // both funnel through here) — so it's the right place to fire both
    // exercise-loop hooks, guaranteeing neither can land mid-sentence.
    // onBotReply is unconditional (every turn); onExerciseRequest only
    // fires when THIS turn ended in a marker, and the ref is already
    // populated by now — see the ref's own comment for why the ordering is
    // safe.
    onBotReply?.();
    if (pendingExercisePatternRef.current) {
      const patternId = pendingExercisePatternRef.current;
      pendingExercisePatternRef.current = null;
      onExerciseRequest?.(patternId);
    }
  };

  const handleFinish = () => {
    if (finishedRef.current) return;
    finishedRef.current = true;
    // AGENT-00X: fire before either branch below — a still-open exercise
    // card must disappear the instant the session winds down, whether it
    // ends up showing the summary modal or going straight back to /practice.
    onSessionEnded?.();
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
    setShowFinishConfirm(false);
    // User-initiated finish → straight back to the list, tandem included
    // (BUG-010, supersedes the TANDEM-001 Phase 4 always-show rule): the
    // debrief still runs server-side once the socket closes and becomes
    // readable in the Development page's recent-sessions block. The in-place
    // summary/debrief modal is reserved for agent-completed sessions, where
    // the learner is naturally at a stopping point.
    if (reason === "user") {
      onFinish();
      return;
    }
    setEndedBy(reason);
    setShowSummary(true);
  };

  // BUG-009 liveness watchdog: the client-side counterpart to the ~25s server
  // heartbeat (pipeline/factory.py::_rtvi_heartbeat). Railway's edge proxy can
  // idle-kill the WS without ever delivering a close frame to the browser
  // (half-open socket) — onDisconnected simply never fires, so the UI is
  // stuck showing LISTENING. Checked every 15s; three missed ~25s heartbeats
  // (>80s of total silence) declares the session dead and routes through the
  // SAME finish path a real disconnect uses. `finishedRef` — flipped by
  // handleFinish itself — is already this component's single "have we wound
  // down yet" guard, so it doubles as the double-fire guard here too. Only
  // armed during the live phase; the interval is torn down on phase change
  // and on unmount.
  useEffect(() => {
    if (phase !== "live") return;
    const LIVENESS_CHECK_MS = 15_000;
    const LIVENESS_TIMEOUT_MS = 80_000; // 3 missed ~25s heartbeats
    const id = setInterval(() => {
      if (finishedRef.current) return;
      if (Date.now() - lastActivityRef.current > LIVENESS_TIMEOUT_MS) {
        void clientRef.current?.disconnect(); // safe no-op if already dead
        handleFinish();
      }
    }, LIVENESS_CHECK_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

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
    // AGENT-001: lessons with `kickoff` open on the agent's turn, not idle — lock
    // Record (and the orb copy) as "agent_thinking" until onBotStartedSpeaking
    // / onBotStoppedSpeaking below drive it through agent_speaking to
    // your_turn. Otherwise the window between connect and the kickoff audio
    // leaves Record clickable over a dead mic.
    setSpeakerState(agentOpens ? "agent_thinking" : "idle");
    // BUG-009: reset the liveness clock here, not just at mount — a learner
    // who sits on the briefing screen past the 80s threshold would otherwise
    // have the watchdog fire the instant the live phase's interval starts.
    lastActivityRef.current = Date.now();
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
        // TAND-003: Practice mode never streams mic audio up — the learner
        // records locally and the finished clip goes through
        // POST /tandem/say-audio instead. Bot audio-out + RTVI messages are
        // unaffected either way (enableMic only gates the transport's own
        // getUserMedia/upstream-audio path, verified against
        // @pipecat-ai/websocket-transport).
        enableMic: !practiceMode,
        callbacks: {
          onConnected: () => setStatus("Connected"),
          // WS close codes are a backstop — entry screens pre-check the bundle/limit
          // so this path is rarely hit. 4001 = not enough coins, 4002 = Clara requires Basic, 4003 = Clara daily limit.
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
            lastActivityRef.current = Date.now(); // BUG-009 liveness signal
            if (data.final && data.text) {
              const text = data.text;
              setMessages((prev) => [...prev, { speaker: "you", text }]);
              // STT gave us the final transcript → LLM is composing now.
              setSpeakerState("agent_thinking");
            }
          },
          onBotOutput: (data: { text?: string; audio_duration_ms?: number }) => {
            lastActivityRef.current = Date.now(); // BUG-009 liveness signal
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
            // BUG-009: every server message — including the heartbeat below —
            // is proof the socket is still alive end-to-end, so this is the
            // primary liveness signal (session_started only fires once).
            lastActivityRef.current = Date.now();
            if (
              data &&
              typeof data === "object" &&
              (data as { type?: unknown }).type === "session_started"
            ) {
              const sid = (data as { session_id?: unknown }).session_id;
              if (typeof sid === "string") {
                setSessionId(sid);
              }
            } else if (
              data &&
              typeof data === "object" &&
              (data as { type?: unknown }).type === "exercise_request"
            ) {
              // AGENT-00X: Clara ended her reply with a `[[ÜBUNG: <id>]]`
              // marker (agents/pipecat_wrapper.py). This arrives while her
              // audio for that reply is still playing — stash the id and let
              // flushPendingBot surface it once the reply's bubble actually
              // reveals, so the card can never pop up mid-sentence.
              const patternId = (data as { pattern_id?: unknown }).pattern_id;
              if (typeof patternId === "string" && patternId) {
                pendingExercisePatternRef.current = patternId;
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
        // TAND-012: tandem chat-length picker (5/10/15 exchanges), backend
        // whitelists the value — see TopicScreen.tsx.
        (params.exchanges ? `&exchanges=${params.exchanges}` : "") +
        // Cold-start slice: teacher's picked focus/starter card's pattern
        // id — see TeacherTopicScreen.tsx / TeacherChat.tsx.
        (params.pattern ? `&pattern=${encodeURIComponent(params.pattern)}` : "") +
        `&token=${encodeURIComponent(token)}`;
      await client.connect({ wsUrl });
    } catch (e) {
      setStatus(`Connection failed: ${e}`);
    }
  };

  // skipBriefing (Clara): fire the exact same connect handler the briefing's
  // "I am ready" button calls, once, right after mount — phase already
  // starts "live" (see the useState above) so there's no briefing screen to
  // wait for a click on.
  useEffect(() => {
    if (!skipBriefing || autoStartedRef.current) return;
    autoStartedRef.current = true;
    void startCall();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skipBriefing]);

  const confirmFinish = () => {
    setShowFinishConfirm(false);
    endReasonRef.current = "user";
    // BUG-010: never await disconnect() here. On a half-open socket the
    // promise can stall indefinitely — and the liveness watchdog can't help,
    // because the server keeps heartbeating over the still-open WS — leaving
    // the learner stuck on a dead live page (2026-07-31 prod repro). Fire it
    // and advance; the transport close proceeds in the background.
    const client = clientRef.current;
    clientRef.current = null;
    if (client) void client.disconnect();
    handleFinish();
  };

  const sendText = async () => {
    const text = draft.trim();
    if (!text || phase !== "live" || !token || !user) return;
    // BUG-005: two fast Enter presses can both pass the guard above before
    // React clears `draft` — bail on the second call.
    if (sendingRef.current) return;
    sendingRef.current = true;
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
      if (!r.ok) {
        // Send failed — still the user's turn, don't leave the orb thinking.
        setStatus(`Send failed: ${r.status}`);
        setSpeakerState("your_turn");
      }
    } catch (e) {
      // Send failed — still the user's turn, don't leave the orb thinking.
      setStatus(`Send error: ${e}`);
      setSpeakerState("your_turn");
    } finally {
      sendingRef.current = false;
    }
  };

  // TAND-003 Practice mode: fires once per completed recording (never for one
  // discarded by unmounting mid-take — see useRecorder). Uploads the clip,
  // appends the returned transcript as a "you" bubble (Natural mode gets that
  // bubble via onUserTranscript, which never fires here since the mic is
  // off), and flips to "agent_thinking" so the UI reads right until the bot's
  // reply streams in. Errors surface inline and never touch `status`/the
  // session — a failed upload can always be retried by recording again.
  const [practiceError, setPracticeError] = useState<string | null>(null);
  const [practiceSending, setPracticeSending] = useState(false);

  const handlePracticeStop = useCallback(
    async (blob: Blob) => {
      if (!token || !user) return;
      setPracticeSending(true);
      setPracticeError(null);
      try {
        const form = new FormData();
        form.append("audio", blob, "practice-turn.webm");
        const r = await fetch(`${HTTP_BASE}/tandem/say-audio`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: form,
        });
        if (!r.ok) {
          if (r.status === 404) {
            // BUG-009: Practice mode has no onDisconnected of its own to catch
            // this — the mic is off, so nothing streams up the WS between
            // takes, and a Railway edge idle-kill can silently tear the
            // pipeline down (debrief already ran) between one recording and
            // the next. The backend's "No active session" 404 IS that
            // disconnect signal here; recover the same way the liveness
            // watchdog does instead of dead-ending on an inline error.
            void clientRef.current?.disconnect(); // safe no-op if already dead
            handleFinish();
            return;
          }
          const detail = (await r.json().catch(() => null))?.detail;
          setPracticeError(
            typeof detail === "string" ? detail : "Couldn't send that — try again."
          );
          return;
        }
        const data: { transcript?: string } = await r.json();
        if (data.transcript) {
          setMessages((prev) => [...prev, { speaker: "you", text: data.transcript! }]);
          setSpeakerState("agent_thinking");
        }
      } catch {
        setPracticeError("Couldn't send that — check your connection and try again.");
      } finally {
        setPracticeSending(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [token, user]
  );

  const recorder = useRecorder(handlePracticeStop);

  // AGENT-00X: single focus, continued — the Record button is hidden below
  // while an exercise is active, but a recording started just before the
  // reveal (during the ~2s pause) can still be in flight; cancel it rather
  // than leave an orphaned take with no visible control to stop it.
  useEffect(() => {
    if (exerciseActive && recorder.recording) recorder.cancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exerciseActive]);

  return (
    <main className="relative min-h-screen overflow-hidden bg-paper text-ink">
      {/* Bauhaus decorations — quieter than SetupView so chat reads cleanly */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-40 -right-40 h-[26rem] w-[26rem] rounded-full bg-flag-gold/25"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-28 -left-24 h-64 w-64 rotate-6 bg-flag-red-fill/70"
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
            onBack={onBack ?? onFinish}
          />
        )}
        {phase === "live" && (
          <LivePhase
            title={meta?.title ?? ""}
            messages={messages}
            speakerState={speakerState}
            transcriptRef={transcriptRef}
            onFinish={() => setShowFinishConfirm(true)}
            prominentFinish={TANDEM_LESSONS.has(params.lesson) || params.lesson === TEACHER_LESSON}
            germanWay={TANDEM_LESSONS.has(params.lesson)}
            onGloss={onGloss}
            onAdd={onAdd}
            practiceMode={practiceMode}
            onOpenType={typedInput ? () => setTypeOpen(true) : undefined}
            recording={recorder.recording}
            elapsed={recorder.elapsed}
            sending={practiceSending}
            recordError={recorder.error ?? practiceError}
            onStartRecording={recorder.start}
            onStopRecording={recorder.stop}
            onCancelRecording={recorder.cancel}
            exerciseSlot={exerciseSlot}
          />
        )}

        <audio ref={audioRef} autoPlay />
      </div>

      {/* Type-a-turn overlay (press /) — slides up from bottom */}
      {phase === "live" && typeOpen && (
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
                {typedInput ? "Type a message" : "Dev · type a turn"}
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
                placeholder={typedInput ? "Ask about German…" : "Hallo, ich heiße…"}
                className="flex-1 rounded-2xl border-[3px] border-line bg-card px-4 py-3 font-display text-[15px] font-semibold text-ink placeholder:text-ink-faint focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
              />
              <button
                onClick={sendText}
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

      {showSummary &&
        (TANDEM_LESSONS.has(params.lesson) ? (
          <TandemDebriefModal
            lessonTitle={meta?.title ?? ""}
            partnerName={partnerByLesson(params.lesson)?.name ?? "Lena"}
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/55 p-4 backdrop-blur-[2px]">
          <div className="lift-panel w-full max-w-[420px] rounded-[28px] border-[3px] border-line bg-elevated px-7 py-8">
            <h2 className="font-display text-[26px] font-black leading-tight text-ink">
              Finish the lesson?
            </h2>
            <div className="mt-7 flex gap-3">
              <button
                onClick={() => setShowFinishConfirm(false)}
                className="btn-3d flex-1 rounded-2xl border-[3px] border-line bg-card px-5 py-3 font-display text-[14px] font-bold uppercase tracking-[0.16em] text-ink"
                style={
                  { ["--shadow-color"]: "var(--color-line)" } as React.CSSProperties
                }
              >
                Keep going
              </button>
              <button
                onClick={confirmFinish}
                className="btn-3d flex-1 rounded-2xl border-[3px] border-red-line bg-flag-red-fill px-5 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-on-fill"
                style={
                  {
                    ["--shadow-color"]: "var(--color-red-line)",
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
          className="rise-in mt-10 rounded-[28px] border-[3px] border-line bg-paper-warm px-7 py-2"
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
        className="btn-3d rise-in mt-8 flex w-full items-center justify-center gap-3 rounded-[28px] border-[3px] border-red-line bg-flag-red-fill px-6 py-5 font-display text-[18px] font-black uppercase tracking-[0.18em] text-on-fill disabled:cursor-wait disabled:opacity-60"
        style={
          {
            ["--shadow-color"]: "var(--color-red-line)",
            animationDelay: "200ms",
          } as React.CSSProperties
        }
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current">
          <path d="M6 4 L20 12 L6 20 Z" />
        </svg>
        I am ready
      </button>

      {/* AI Act Art. 50(1): in-product disclosure, shown before every
          session — see Privacy Policy §8. Additive-only: no behavior/timing
          change to the briefing flow itself. */}
      <p
        className="rise-in mt-3 text-center font-body text-[12px] leading-relaxed text-ink-faint"
        style={{ animationDelay: "220ms" }}
      >
        You&apos;ll be speaking with an AI conversation partner — the voice
        is computer-generated.
      </p>

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
              className="mt-2.5 inline-block h-2 w-2 shrink-0 rounded-full bg-ink-fill"
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
  prominentFinish,
  germanWay,
  onGloss,
  onAdd,
  practiceMode,
  recording,
  elapsed,
  sending,
  recordError,
  onStartRecording,
  onStopRecording,
  onCancelRecording,
  onOpenType,
  exerciseSlot,
}: {
  title: string;
  messages: ChatMessage[];
  speakerState: SpeakerState;
  transcriptRef: React.RefObject<HTMLElement | null>;
  onFinish: () => void;
  // TAND-004: tandem sessions end by user choice — the exchange cap is a
  // rarely-hit backstop — so tandem gets an unmissable primary button here
  // instead of the small "✕ Finish" other lessons keep.
  prominentFinish?: boolean;
  // IDIOM-002 P1: tandem only — the on-demand "How would a German say
  // this?" link under the learner's own bubbles. Clara and the /learn
  // lessons never see it.
  germanWay?: boolean;
  // UI-009: word-gloss popover wiring, forwarded from ConversationView —
  // see that component's props for the contract.
  onGloss?: (word: string, context: string) => Promise<GlossInfo>;
  onAdd?: (lemma: string) => Promise<{ glossRemaining?: number } | void>;
  // TAND-003: Practice input mode — all seven are only meaningful (and only
  // ever passed as real values) when practiceMode is true. Natural mode
  // (practiceMode undefined/false, the untouched default) renders none of
  // the record-control JSX below.
  practiceMode?: boolean;
  recording?: boolean;
  elapsed?: number;
  sending?: boolean;
  recordError?: string | null;
  onStartRecording?: () => void;
  onStopRecording?: () => void;
  // UI-006: escape hatch for a take the learner doesn't want sent.
  onCancelRecording?: () => void;
  // AGENT-001: opens the type-a-turn overlay from a visible button, not just
  // the dev-only "/" shortcut.
  onOpenType?: () => void;
  // CLARA-13: the exercise slot, forwarded verbatim from ConversationView —
  // see that component's props for the contract. Absent for
  // VoiceChat/TandemChat.
  exerciseSlot?: React.ReactNode;
}) {
  const orbClass = `orb orb-${speakerState.replace("_", "-")}`;
  // No barge-in by design: while Lena is composing or speaking, recording a
  // new turn is disabled — same rule the streaming path enforces implicitly
  // (STTMuteFilter mutes the mic while the bot talks; see pipeline/factory.py).
  const botBusy =
    speakerState === "agent_thinking" || speakerState === "agent_speaking";
  const recordDisabled = !recording && (botBusy || !!sending);
  // CLARA-13: single focus — while the slot is up, Record and Type are
  // hidden outright (not just disabled), and the shortcut that opens Type is
  // ignored (see ConversationView's keydown effect). Gated purely on
  // `exerciseSlot` being present, same as every other focus-mode check.
  const exerciseActive = exerciseSlot != null;
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
        {prominentFinish ? (
          <button
            onClick={onFinish}
            aria-label="End conversation"
            className="btn-3d shrink-0 rounded-full border-[3px] border-line bg-ink-fill px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.18em] text-on-fill transition-colors hover:bg-flag-red-fill hover:border-red-line"
            style={
              {
                ["--shadow-color"]: "var(--color-line)",
              } as React.CSSProperties
            }
          >
            End conversation
          </button>
        ) : (
          <button
            onClick={onFinish}
            aria-label="Finish lesson"
            className="btn-3d shrink-0 rounded-2xl border-[3px] border-line bg-card px-4 py-2 font-display text-[12px] font-bold uppercase tracking-[0.18em] text-ink hover:bg-flag-red-fill hover:text-on-fill hover:border-red-line transition-colors"
            style={
              {
                ["--shadow-color"]: "var(--color-line)",
              } as React.CSSProperties
            }
          >
            ✕ Finish
          </button>
        )}
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
          {practiceMode
            ? PRACTICE_STATE_LABEL[speakerState]
            : STATE_LABEL[speakerState]}
        </p>
      </div>

      {/* TAND-003 Practice mode: tap-record / tap-stop, auto-sends on stop —
          same interaction language as SprechenTrainer/SzenarioTrainer's own
          record controls. Hidden entirely in Natural mode, and hidden (not
          just disabled) while an exercise card is up — AGENT-00X single
          focus. */}
      {practiceMode && !exerciseActive && (
        <div
          className="rise-in flex flex-col items-center gap-2 pb-2"
          style={{ animationDelay: "120ms" }}
        >
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={recording ? onStopRecording : onStartRecording}
              disabled={recordDisabled}
              className={`btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] px-7 py-3.5 font-display text-[14px] font-black uppercase tracking-[0.16em] disabled:cursor-not-allowed disabled:opacity-50 ${
                recording
                  ? "animate-pulse border-red-line bg-card text-flag-red"
                  : "border-red-line bg-flag-red-fill text-on-fill"
              }`}
              style={
                { ["--shadow-color"]: "var(--color-red-line)" } as React.CSSProperties
              }
            >
              {recording ? `Stop · ${elapsed}s` : sending ? "Sending…" : "Record"}
            </button>
            {/* UI-006: visible only mid-recording — the escape hatch for a
                take you don't want sent. */}
            {recording && (
              <button
                type="button"
                onClick={onCancelRecording}
                aria-label="Discard recording"
                title="Discard recording"
                className="btn-3d inline-flex h-[52px] w-[52px] items-center justify-center rounded-[20px] border-[3px] border-line bg-card font-display text-[18px] font-black text-ink"
                style={{ ["--shadow-color"]: "var(--color-line)" } as React.CSSProperties}
              >
                ✕
              </button>
            )}
          </div>
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
            {recording
              ? "speak, then tap stop — ✕ discards"
              : botBusy
                ? "wait for your partner to finish"
                : sending
                  ? "sending your turn…"
                  : "tap record, then speak"}
          </p>
          {recordError && (
            <p className="max-w-[320px] text-center font-body text-[13px] font-semibold text-flag-red-deep">
              {recordError}
            </p>
          )}
        </div>
      )}

      {/* AGENT-001: visible text-input entry — same overlay "/" opens.
          AGENT-00X: hidden while an exercise card is up — single focus. */}
      {onOpenType && !exerciseActive && (
        <div
          className="rise-in flex justify-center pb-2"
          style={{ animationDelay: "120ms" }}
        >
          <button
            type="button"
            onClick={onOpenType}
            className="btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] border-line bg-card px-6 py-3 font-display text-[13px] font-black uppercase tracking-[0.16em] text-ink"
            style={{ ["--shadow-color"]: "var(--color-line)" } as React.CSSProperties}
          >
            ⌨ Type instead
          </button>
        </div>
      )}

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
            {m.speaker === "you" && germanWay ? (
              // IDIOM-002 P1: the learner's bubble grows a small on-demand
              // "more German way" link underneath. The partner's last line
              // before this turn rides along as judge context — background
              // only, never graded (the judge's prompt says so).
              <div className="flex min-w-0 flex-col items-end">
                <div className="bubble bubble-you">{m.text}</div>
                <GermanWay
                  variant="inline"
                  text={m.text}
                  context={
                    messages
                      .slice(0, i)
                      .reverse()
                      .find((x) => x.speaker === "bot")?.text
                  }
                  onGloss={onGloss}
                  onAdd={onAdd}
                />
              </div>
            ) : (
              <div className={`bubble ${m.speaker === "you" ? "bubble-you" : "bubble-them"}`}>
                {/* UI-009: only the partner's own words get glossed — the
                    learner's bubbles stay plain, they wrote them. */}
                {m.speaker === "bot" && onGloss ? (
                  <Glossable text={m.text} onGloss={onGloss} onAdd={onAdd} />
                ) : (
                  m.text
                )}
              </div>
            )}
          </div>
        ))}
      </section>

      {/* CLARA-13: Clara's interactive-exercise slot — inline in the chat
          flow, after the last bubble, never a modal overlay. TeacherChat
          decides WHEN `exerciseSlot` goes non-null (bot-reply reveal + a
          fixed pause) and owns whatever's rendered inside it (a real drill
          trainer plus its own Skip row, remounted per exercise via its own
          `key`); this just renders it in place and is the only reason
          Record/Type were hidden above. */}
      {exerciseActive && (
        <div className="exercise-reveal mt-4 flex justify-center">
          {exerciseSlot}
        </div>
      )}
    </>
  );
}
