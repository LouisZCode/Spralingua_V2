"use client";

import { useRef, useState, useCallback } from "react";
import { PipecatClient } from "@pipecat-ai/client-js";
import {
  WebSocketTransport,
  ProtobufFrameSerializer,
} from "@pipecat-ai/websocket-transport";

// Generate a random user ID per session (later: replace with auth)
const USER_ID = crypto.randomUUID();
const BASE_WS = "ws://localhost:8765";
const HTTP_BASE = "http://localhost:8765";

interface LogEntry {
  time: string;
  text: string;
  type: "user" | "bot" | "system" | "error";
}

const LEVELS: Record<string, { label: string; situations: Record<string, string> }> = {
  A1: { label: "A1 - Beginner", situations: { introducing_yourself: "Introducing Yourself" } },
  B2: { label: "B2 - Upper Intermediate", situations: { planning_picnic: "Planning a Picnic" } },
};

// Keys must match services/tts.py::VOICE_MAP. Kept in sync with the
// MiniMax account — see ARCHITECTURE.md "Voice Inventory".
const VOICES: Record<string, string> = {
  German_Female: "German Female",
  "German-Male": "German Male",
  luis_clone: "Luis (Clone)",
};

export default function VoiceChat() {
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState("Disconnected");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [level, setLevel] = useState("A1");
  const [situation, setSituation] = useState("introducing_yourself");
  const [voice, setVoice] = useState("German_Female");
  const [draft, setDraft] = useState("");
  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  const log = useCallback((text: string, type: LogEntry["type"] = "system") => {
    const time = new Date().toLocaleTimeString();
    setLogs((prev) => [...prev, { time, text, type }]);
  }, []);

  const connect = useCallback(async () => {
    log("Connecting...");
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
          onConnected: () => {
            log("Connected");
            setStatus("Connected");
            setConnected(true);
          },
          onDisconnected: () => {
            log("Disconnected");
            setStatus("Disconnected");
            setConnected(false);
            clientRef.current = null;
          },
          onTransportStateChanged: (state: string) => {
            log(`Transport: ${state}`);
            setStatus(state);
          },
          onBotReady: () => log("Bot ready"),
          onTrackStarted: (track: MediaStreamTrack, participant?: { local?: boolean }) => {
            if (track.kind === "audio" && participant?.local === false) {
              const stream = new MediaStream([track]);
              if (audioRef.current) {
                audioRef.current.srcObject = stream;
                audioRef.current.play().catch((e) =>
                  log(`Audio play error: ${e}`, "error")
                );
              }
            }
          },
          onUserTranscript: (data: { final?: boolean; text?: string }) => {
            if (data.final) log(`You: ${data.text}`, "user");
          },
          onBotOutput: (data: { text?: string }) => {
            log(`Bot: ${data.text}`, "bot");
          },
          onError: (err: unknown) => {
            log(`Error: ${JSON.stringify(err)}`, "error");
          },
        },
      });

      clientRef.current = client;
      const wsUrl = `${BASE_WS}/ws/${USER_ID}?level=${level}&situation=${situation}&voice=${voice}`;
      await client.connect({ wsUrl });
    } catch (e) {
      log(`Connection failed: ${e}`, "error");
      setConnected(false);
    }
  }, [log, level, situation, voice]);

  const disconnect = useCallback(async () => {
    if (clientRef.current) {
      await clientRef.current.disconnect();
      clientRef.current = null;
    }
    if (audioRef.current?.srcObject) {
      (audioRef.current.srcObject as MediaStream)
        .getTracks()
        .forEach((t) => t.stop());
      audioRef.current.srcObject = null;
    }
  }, []);

  // Type-a-turn: POSTs to /say/{user_id}; backend injects frames as if STT had
  // produced them. The agent still replies via the normal TTS audio path.
  const sendText = useCallback(async () => {
    const text = draft.trim();
    if (!text || !connected) return;
    setDraft("");
    log(`You (typed): ${text}`, "user");
    try {
      const r = await fetch(`${HTTP_BASE}/say/${USER_ID}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) log(`Send failed: ${r.status}`, "error");
    } catch (e) {
      log(`Send error: ${e}`, "error");
    }
  }, [draft, connected, log]);

  const logColor: Record<LogEntry["type"], string> = {
    user: "text-blue-400",
    bot: "text-green-400",
    system: "text-slate-400",
    error: "text-red-400",
  };

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-full max-w-md rounded-xl bg-slate-800 p-8">
        <h1 className="mb-6 text-center text-2xl font-bold">
          Spralingua Voice Chat
        </h1>

        <p className="mb-4 text-center text-sm text-slate-400">
          Status: <span className="font-semibold text-slate-200">{status}</span>
        </p>

        <div className="mb-4 grid grid-cols-3 gap-3">
          <div>
            <label className="mb-1 block text-xs text-slate-400">Level</label>
            <select
              value={level}
              onChange={(e) => {
                setLevel(e.target.value);
                const firstSituation = Object.keys(LEVELS[e.target.value].situations)[0];
                setSituation(firstSituation);
              }}
              disabled={connected}
              className="w-full rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-200 disabled:opacity-40"
            >
              {Object.entries(LEVELS).map(([key, val]) => (
                <option key={key} value={key}>{val.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Situation</label>
            <select
              value={situation}
              onChange={(e) => setSituation(e.target.value)}
              disabled={connected}
              className="w-full rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-200 disabled:opacity-40"
            >
              {Object.entries(LEVELS[level].situations).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Voice</label>
            <select
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              disabled={connected}
              className="w-full rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-200 disabled:opacity-40"
            >
              {Object.entries(VOICES).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="mb-6 flex gap-3">
          <button
            onClick={connect}
            disabled={connected}
            className="flex-1 rounded-lg bg-green-500 py-3 font-semibold text-slate-900 transition-opacity hover:bg-green-400 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Connect
          </button>
          <button
            onClick={disconnect}
            disabled={!connected}
            className="flex-1 rounded-lg bg-red-500 py-3 font-semibold text-white transition-opacity hover:bg-red-400 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Disconnect
          </button>
        </div>

        <div className="h-64 overflow-y-auto rounded-lg bg-slate-900 p-4 font-mono text-xs leading-relaxed">
          {logs.map((entry, i) => (
            <div key={i} className={logColor[entry.type]}>
              [{entry.time}] {entry.text}
            </div>
          ))}
          <div ref={logEndRef} />
        </div>

        <div className="mt-3 flex gap-2">
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") sendText(); }}
            disabled={!connected}
            placeholder={connected ? "Type your turn..." : "Connect first"}
            className="flex-1 rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 disabled:opacity-40"
          />
          <button
            onClick={sendText}
            disabled={!connected || !draft.trim()}
            className="rounded-lg bg-blue-500 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Send
          </button>
        </div>

        <audio ref={audioRef} autoPlay />
      </div>
    </div>
  );
}
