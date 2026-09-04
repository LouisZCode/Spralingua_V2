"use client";

import { useEffect, useRef, useState } from "react";
import Round1Panel from "./Round1Panel";
import Round2Panel from "./Round2Panel";
import Transcript from "./Transcript";
import type { AnswerResult, ComprehensionResult, InterviewItem } from "./api";
import { safeMessage } from "../shared/copy";

const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;
const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;

const KIND_LABEL: Record<string, string> = {
  question: "Question",
  statement: "Statement",
  monologue: "Monologue",
};

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const s = Math.floor(seconds);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}:${String(rem).padStart(2, "0")}`;
}

type Round = "round1" | "round2";

// The per-item practice view: chunk navigation, the audio player (presigned
// URL fetched fresh per chunk — see interview/api.ts::fetchAudioUrl), the
// round-1 listen-lock, the rolling transcript, and the round-1/round-2
// switch. Ported from interview_local/web/player.js's learner-mode path
// (its review-mode/curation half — sidebar, brief editor, segment editing —
// is out of scope for this slice entirely, see interview/routes.py's
// contract: there's no edit/review endpoint to call).
//
// Every chunk the backend returns is already learner-visible (the importer
// only ever registers approved chunks — interview/routes.py's module
// docstring), unlike the workbench's own auto_skipped/rejected filtering,
// so there's no client-side chunk filtering to reproduce here.
export default function InterviewPlayer({
  item,
  error,
  onBack,
  onFetchAudioUrl,
  onComprehension,
  onAnswer,
}: {
  item: InterviewItem | null; // null = loading
  error: boolean;
  onBack: () => void;
  onFetchAudioUrl: (chunkId: string) => Promise<string>;
  onComprehension: (chunkId: string, audio: Blob) => Promise<ComprehensionResult>;
  onAnswer: (chunkId: string, audio: Blob) => Promise<AnswerResult>;
}) {
  const [chunkIndex, setChunkIndex] = useState(0);
  const [round, setRound] = useState<Round>("round1");
  const [round1Passed, setRound1Passed] = useState<boolean | null>(null);

  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [audioError, setAudioError] = useState<string | null>(null);
  const [audioLoading, setAudioLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  // Round-1 listen-lock: the retell record button stays disabled until the
  // learner has cumulatively listened to >=75% of the chunk (or it ends) —
  // ported from player.js's resetListenLock/onListenTimeUpdate/unlockListen.
  const [listenUnlocked, setListenUnlocked] = useState(false);
  const listenedSecondsRef = useRef(0);
  const listenLastTimeRef = useRef(0);
  const listenDurationRef = useRef<number>(NaN);

  const audioRef = useRef<HTMLAudioElement | null>(null);

  const chunk = item?.chunks[chunkIndex] ?? null;
  const hasRound1 = !!chunk?.brief?.summary && chunk.brief.summary.trim().length > 0;

  // Fresh round + listen-lock state on every chunk visit — re-entering a
  // chunk always restarts at round 1, same as the workbench (round state is
  // per-visit, never persisted).
  useEffect(() => {
    if (!chunk) return;
    setRound(hasRound1 ? "round1" : "round2");
    setRound1Passed(null);
    setCurrentTime(0);
    setPlaying(false);
    listenedSecondsRef.current = 0;
    listenLastTimeRef.current = 0;
    listenDurationRef.current = NaN;
    setListenUnlocked(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chunk?.id]);

  // A fresh presigned URL every time the chunk changes (~15-min TTL — see
  // interview/routes.py's GET /audio/{chunk_id} docstring — never cached
  // across a chunk switch).
  useEffect(() => {
    if (!chunk) return;
    let cancelled = false;
    setAudioUrl(null);
    setAudioError(null);
    setAudioLoading(true);
    onFetchAudioUrl(chunk.id)
      .then((url) => {
        if (!cancelled) setAudioUrl(url);
      })
      .catch((e) => {
        if (!cancelled) {
          setAudioError(safeMessage(e, "Couldn't load the audio — try again."));
        }
      })
      .finally(() => {
        if (!cancelled) setAudioLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chunk?.id]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audioUrl) {
      audio.src = audioUrl;
      audio.load();
    } else {
      // Chunk switch in flight (a fresh URL fetch just started, see the
      // effect above) — stop and clear immediately rather than leaving the
      // PREVIOUS chunk's audio playing in the background until the new URL
      // lands.
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
  }, [audioUrl]);

  function pauseAudio() {
    // Never record over the interviewer's own voice.
    audioRef.current?.pause();
  }

  function togglePlay() {
    const audio = audioRef.current;
    if (!audio || !audioUrl) return;
    if (audio.paused || audio.ended) {
      if (audio.ended) audio.currentTime = 0;
      void audio.play();
    } else {
      audio.pause();
    }
  }

  function onSeekChange(value: number) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = value;
    setCurrentTime(value);
  }

  function trackListenProgress(cur: number) {
    if (listenUnlocked) return;
    const delta = cur - listenLastTimeRef.current;
    listenLastTimeRef.current = cur;
    // Only forward progress up to 1s counts (timeupdate fires ~4/s) — a
    // bigger jump is a seek, forward or back, and must never count.
    if (delta > 0 && delta <= 1.0) listenedSecondsRef.current += delta;
    const dur = listenDurationRef.current;
    if (Number.isFinite(dur) && dur > 0 && listenedSecondsRef.current >= 0.75 * dur) {
      setListenUnlocked(true);
    }
  }

  function resyncListenTime() {
    if (audioRef.current) listenLastTimeRef.current = audioRef.current.currentTime;
  }

  if (error) {
    return (
      <div className="mx-auto max-w-2xl text-center">
        <p className="font-body text-[14px] font-semibold text-flag-red-deep">
          Couldn&apos;t load that interview — try again.
        </p>
        <BackButton onBack={onBack} />
      </div>
    );
  }

  if (!item) {
    return (
      <p className="text-center font-body text-[12px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
        Loading…
      </p>
    );
  }

  if (!chunk) {
    return (
      <div className="mx-auto max-w-2xl text-center">
        <p className="font-body text-[14px] font-semibold text-ink-soft">
          This interview has no chunks yet.
        </p>
        <BackButton onBack={onBack} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-5 flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
        >
          ← All interviews
        </button>
        <span className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-muted">
          {chunkIndex + 1} / {item.chunks.length}
        </span>
      </div>

      <div className="mb-5 text-center">
        <h1 className="font-display text-[22px] font-black tracking-tight text-ink">
          {item.company}
        </h1>
        <p className="mt-1 font-body text-[12px] font-semibold uppercase tracking-[0.2em] text-ink-muted">
          {[item.interviewer, item.level].filter(Boolean).join(" · ")}
        </p>
      </div>

      {/* Spoken-German interview audio, no captions track exists — the
          transcript below serves that role once round 2 reveals it. */}
      <audio
        ref={audioRef}
        preload="metadata"
        className="hidden"
        onTimeUpdate={() => {
          const cur = audioRef.current?.currentTime ?? 0;
          setCurrentTime(cur);
          trackListenProgress(cur);
        }}
        onLoadedMetadata={() => {
          const audio = audioRef.current;
          if (!audio) return;
          setDuration(audio.duration);
          listenDurationRef.current = audio.duration;
        }}
        onPlay={() => {
          setPlaying(true);
          resyncListenTime();
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false);
          setListenUnlocked(true);
        }}
        onSeeking={resyncListenTime}
        onSeeked={resyncListenTime}
      />

      <div className="rounded-[20px] border-[3px] border-line bg-card px-5 py-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={togglePlay}
            disabled={!audioUrl}
            aria-label={playing ? "Pause" : "Play"}
            className="btn-3d flex h-12 w-12 shrink-0 items-center justify-center rounded-full border-[3px] border-line bg-card font-display text-[16px] font-black text-ink disabled:opacity-40"
            style={inkShadow}
          >
            {playing ? "❚❚" : "▶"}
          </button>
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.1}
            value={Math.min(currentTime, duration || 0)}
            disabled={!audioUrl}
            onChange={(e) => onSeekChange(Number(e.target.value))}
            className="h-1.5 flex-1 accent-flag-red"
            aria-label="Seek"
          />
          <span className="w-[80px] shrink-0 text-right font-body text-[12px] font-semibold text-ink-muted">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
        </div>
        {audioLoading && (
          <p className="mt-2 font-body text-[12px] text-ink-muted">Loading audio…</p>
        )}
        {audioError && (
          <p className="mt-2 font-body text-[12px] font-semibold text-flag-red-deep">
            {audioError}
          </p>
        )}
        {chunk.kind && (
          <p className="mt-2 font-body text-[10px] font-bold uppercase tracking-[0.18em] text-ink-muted">
            {KIND_LABEL[chunk.kind] ?? chunk.kind}
          </p>
        )}
      </div>

      {/* Round 1's whole point is retelling from memory — the transcript
          never renders while it's live. Round 2 (or a legacy, brief-less
          chunk that skips round 1 entirely) reveals it, synced to
          playback. */}
      {round === "round2" && (
        <div className="mt-5">
          <Transcript
            segments={chunk.segments}
            currentTime={currentTime}
            fallbackText={chunk.transcript}
          />
        </div>
      )}

      <div className="mt-5">
        {round === "round1" ? (
          <Round1Panel
            key={chunk.id}
            chunkId={chunk.id}
            listenUnlocked={listenUnlocked}
            onBeforeRecord={pauseAudio}
            onSubmit={onComprehension}
            onDone={(passed) => {
              setRound1Passed(passed);
              setRound("round2");
            }}
          />
        ) : (
          <Round2Panel
            key={chunk.id}
            chunkId={chunk.id}
            brief={chunk.brief}
            round1Passed={hasRound1 ? round1Passed : null}
            onBeforeRecord={pauseAudio}
            onSubmit={onAnswer}
          />
        )}
      </div>

      <nav className="mt-7 flex items-center justify-between">
        <button
          type="button"
          disabled={chunkIndex <= 0}
          onClick={() => setChunkIndex((i) => i - 1)}
          className="btn-3d inline-flex items-center rounded-[16px] border-[3px] border-line bg-card px-5 py-2.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-ink disabled:cursor-not-allowed disabled:opacity-40"
          style={inkShadow}
        >
          ← Prev
        </button>
        <button
          type="button"
          disabled={chunkIndex >= item.chunks.length - 1}
          onClick={() => setChunkIndex((i) => i + 1)}
          className="btn-3d inline-flex items-center rounded-[16px] border-[3px] border-red-line bg-flag-red-fill px-5 py-2.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-on-fill disabled:cursor-not-allowed disabled:opacity-40"
          style={redShadow}
        >
          Next →
        </button>
      </nav>
    </div>
  );
}

function BackButton({ onBack }: { onBack: () => void }) {
  return (
    <button
      type="button"
      onClick={onBack}
      className="mt-4 font-body text-[12px] font-bold uppercase tracking-[0.2em] text-ink-muted transition-colors hover:text-flag-red"
    >
      ← All interviews
    </button>
  );
}
