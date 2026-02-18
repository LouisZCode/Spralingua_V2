"use client";

import { useRef, useState, useCallback } from "react";
import { PipecatClient } from "@pipecat-ai/client-js";
import {
  WebSocketTransport,
  ProtobufFrameSerializer,
} from "@pipecat-ai/websocket-transport";

// Generate a random user ID per session (later: replace with auth)
const USER_ID = crypto.randomUUID();
const WS_URL = `ws://localhost:8765/ws/${USER_ID}`;

interface LogEntry {
  time: string;
  text: string;
  type: "user" | "bot" | "system" | "error";
}

export default function VoiceChat() {
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState("Disconnected");
  const [logs, setLogs] = useState<LogEntry[]>([]);
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
      await client.connect({ wsUrl: WS_URL });
    } catch (e) {
      log(`Connection failed: ${e}`, "error");
      setConnected(false);
    }
  }, [log]);

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

        <audio ref={audioRef} autoPlay />
      </div>
    </div>
  );
}
