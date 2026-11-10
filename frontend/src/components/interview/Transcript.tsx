"use client";

import { useEffect, useRef } from "react";
import type { Segment } from "./api";

// Teleprompter, synced to <audio> playback via `currentTime` (t0/t1 are
// seconds relative to the chunk's own mp3 — see interview/routes.py's
// GET /items/{id} contract). Round 2 / legacy (brief-less) chunks only:
// Round 1 never mounts this at all (InterviewPlayer keeps it out of the
// tree entirely while round 1 is live) — the whole point of that round is
// retelling from memory, not reading along.
//
// A faithful port of the workbench teleprompter (player.js::
// getCenterSegment/updateTeleprompter + style.css .transcript.teleprompter):
// the box is a FIXED viewport — it never scrolls. The segment matching the
// current time is pinned to the viewport's vertical middle and rendered
// crisp and bold; every other line is blurred and dimmed so the learner
// cannot read ahead. As playback advances the whole track glides upward
// (translateY on the track, 350ms), the finished line drifting up out of
// focus while the next rises into the center — a carousel, not a moving
// highlight. Between two segments (a pause) the most recently finished one
// stays centered/crisp rather than the box going blank; before the first
// segment starts, segment 0 is centered but dimmed with nothing crisp yet.
// Empty space above the first line / below the last is expected.
function findActiveIndex(segments: Segment[], t: number): number {
  for (let i = 0; i < segments.length; i++) {
    if (t >= segments[i].t0 && t < segments[i].t1) return i;
  }
  return -1;
}

function centerSegment(
  segments: Segment[],
  t: number
): { idx: number; active: boolean } | null {
  if (!segments.length) return null;
  const activeIdx = findActiveIndex(segments, t);
  if (activeIdx !== -1) return { idx: activeIdx, active: true };
  let lastPast = -1;
  for (let i = 0; i < segments.length; i++) {
    if (segments[i].t1 <= t) lastPast = i;
    else break; // segments are chronological
  }
  if (lastPast === -1) return { idx: 0, active: false };
  return { idx: lastPast, active: true };
}

export default function Transcript({
  segments,
  currentTime,
  fallbackText,
}: {
  segments: Segment[];
  currentTime: number;
  // A handful of imported chunks carry a transcript but no per-word segment
  // timings — show the plain text rather than an empty box in that case.
  fallbackText?: string;
}) {
  const center = centerSegment(segments, currentTime);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const activeRef = useRef<HTMLParagraphElement | null>(null);

  // Pin the center segment to the viewport's middle. offsetTop/offsetHeight
  // are read AFTER the active class has flipped and rendered, so they
  // include the active line's larger font box (the workbench deliberately
  // does not transition font-size for exactly this measurement — same
  // here: only transform/filter/opacity/color transition, never size).
  useEffect(() => {
    const vp = viewportRef.current;
    const el = activeRef.current;
    if (!vp || !el) return;
    const recenter = () => {
      const track = el.offsetParent as HTMLElement | null;
      if (!track) return;
      // offsetTop is relative to the track (position: relative), unaffected
      // by the track's own transform — measure the untransformed layout.
      const y = vp.clientHeight / 2 - (el.offsetTop + el.offsetHeight / 2);
      track.style.transform = `translateY(${y}px)`;
    };
    recenter();
    window.addEventListener("resize", recenter);
    return () => window.removeEventListener("resize", recenter);
  }, [center?.idx, center?.active]);

  if (!segments.length) {
    return fallbackText ? (
      <div className="min-h-[200px] max-h-[50vh] overflow-y-auto rounded-[18px] border-[3px] border-line bg-card px-4 py-2">
        <p className="font-body text-[22px] leading-relaxed text-ink">{fallbackText}</p>
      </div>
    ) : (
      <p className="font-body text-[13px] text-ink-muted">
        No transcript for this chunk.
      </p>
    );
  }

  return (
    <div
      ref={viewportRef}
      className="relative h-[46vh] min-h-[260px] max-h-[480px] overflow-hidden rounded-[28px] border-[3px] border-line bg-card shadow-[0_6px_0_var(--color-line)]"
    >
      {/* Top/bottom fade masks: far lines dissolve into the card instead of
          sitting there blurred at full opacity (workbench ::before/::after). */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 z-10 h-[34%] bg-gradient-to-b from-card via-card/60 to-transparent"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-[34%] bg-gradient-to-t from-card via-card/60 to-transparent"
      />
      <div className="relative will-change-transform [transition:transform_350ms_ease]">
        {segments.map((seg, i) => {
          const isCenter = center?.idx === i;
          const isActive = isCenter && center?.active;
          return (
            <p
              key={i}
              ref={isCenter ? activeRef : undefined}
              className={`border-l-4 px-4 py-3.5 font-body leading-relaxed [transition:filter_320ms_ease,opacity_320ms_ease,color_320ms_ease] ${
                isActive
                  ? "border-flag-red bg-flag-red-soft text-[24px] font-bold text-ink opacity-100 blur-0"
                  : "border-transparent text-[18px] text-ink-soft opacity-40 blur-[3px]"
              }`}
            >
              {seg.text}
            </p>
          );
        })}
      </div>
    </div>
  );
}
