"use client";

import { useEffect, useRef } from "react";
import type { Segment } from "./api";

// Rolling transcript, synced to <audio> playback via `currentTime` (t0/t1
// are seconds relative to the chunk's own mp3 — see interview/routes.py's
// GET /items/{id} contract). Round 2 / legacy (brief-less) chunks only:
// Round 1 never mounts this at all (InterviewPlayer keeps it out of the
// tree entirely while round 1 is live) — the whole point of that round is
// retelling from memory, not reading along.
//
// A faithful-but-simplified port of the workbench's teleprompter
// (player.js::getCenterSegment/updateTeleprompter): the segment matching
// the current time renders crisp and centered; between two segments (a
// pause) the most recently finished one stays crisp rather than the box
// going blank; before the first segment starts, segment 0 shows dimmed
// with nothing crisp yet. The workbench glides a translateY track to hold
// the center segment at the viewport's vertical middle — this uses
// scrollIntoView within a scrollable box instead, which reads the same to
// a learner without reproducing that CSS transform math.
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
  const activeRef = useRef<HTMLParagraphElement | null>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
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

  // Typography mirrors the workbench's review-mode segment list
  // (interview_local/web/style.css .segment: 22px rows, active = soft-red
  // bg + 4px red left bar + semibold, past rows dimmed) — Luis asked for
  // the exercise's reading box to read the same as the curation area
  // instead of the old flat 15px/220px box. Tokens only, so dark mode
  // keeps working (flag-red lifts for text-adjacent use; the fill is not
  // used here).
  return (
    <div className="min-h-[200px] max-h-[50vh] overflow-y-auto rounded-[18px] border-[3px] border-line bg-card px-2 py-2">
      {segments.map((seg, i) => {
        const isCenter = center?.idx === i;
        const isActive = isCenter && center?.active;
        const isPast = center !== null && i < center.idx;
        return (
          <p
            key={i}
            ref={isCenter ? activeRef : undefined}
            className={`rounded-[6px] border-l-4 px-3 py-2 font-body text-[22px] leading-relaxed transition-colors duration-300 ${
              isActive
                ? "border-flag-red bg-flag-red-soft font-semibold text-ink"
                : isPast
                  ? "border-transparent text-ink-soft opacity-60"
                  : "border-transparent text-ink-muted"
            }`}
          >
            {seg.text}
          </p>
        );
      })}
    </div>
  );
}
