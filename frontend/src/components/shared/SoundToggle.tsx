"use client";

// GAME-001: app-wide mute switch for the synthesized earcons (shared/sound.ts).
// Mounted next to the header controls on every page that plays sounds.

import { useEffect, useState } from "react";
import { isMuted, setMuted } from "./sound";

const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

export default function SoundToggle() {
  // SSR-safe: localStorage isn't readable during render, so hydrate the real
  // value in an effect (same idiom as Flow.tsx's round-choice hydration).
  const [muted, setMutedState] = useState(false);

  useEffect(() => {
    setMutedState(isMuted());
  }, []);

  function toggle() {
    const next = !muted;
    setMuted(next);
    setMutedState(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={muted}
      title="Toggle sounds"
      className="btn-3d inline-flex h-10 w-10 items-center justify-center rounded-2xl border-[3px] border-ink bg-white text-ink"
      style={inkShadow}
    >
      {muted ? (
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4.5 w-4.5"
        >
          <path d="M11 5 6 9H3v6h3l5 4V5Z" />
          <path d="m17 9 4 6M21 9l-4 6" />
        </svg>
      ) : (
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4.5 w-4.5"
        >
          <path d="M11 5 6 9H3v6h3l5 4V5Z" />
          <path d="M15.5 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11" />
        </svg>
      )}
    </button>
  );
}
