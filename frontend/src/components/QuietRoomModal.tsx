"use client";

import { createPortal } from "react-dom";

// Weekly "set up to speak" nudge shown on the lesson setup screen: Spralingua is
// a speaking app, so steer the learner to a quiet room + headphones before they
// start talking. The once-per-week cadence is owned by SetupView (localStorage).
// Portaled to <body> so the rise-in transforms on the setup screen can't trap
// the fixed overlay (same gotcha SignInModal documents).
export default function QuietRoomModal({ onClose }: { onClose: () => void }) {
  return createPortal(
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-scrim/60 p-6 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="lift-panel relative w-full max-w-sm rounded-[28px] border-[3px] border-line bg-elevated p-8 text-center"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mx-auto grid h-16 w-16 place-items-center rounded-full border-[3px] border-line bg-flag-gold">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.2}
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-8 w-8 text-ink"
          >
            <path d="M5 15v-3a7 7 0 0 1 14 0v3" />
            <rect
              x="3.4"
              y="14.5"
              width="3.8"
              height="5.6"
              rx="1.7"
              fill="currentColor"
              stroke="none"
            />
            <rect
              x="16.8"
              y="14.5"
              width="3.8"
              height="5.6"
              rx="1.7"
              fill="currentColor"
              stroke="none"
            />
          </svg>
        </div>

        <h2 className="mt-5 font-display text-[24px] font-black leading-tight text-ink">
          Tip: find a quiet space
        </h2>
        <p className="mx-auto mt-3 max-w-[280px] font-body text-[14px] leading-relaxed text-ink-soft">
          Spralingua is a speaking app — for the best practice, find a quiet room
          and pop in headphones if you can.
          <br />
          Then just talk naturally!
        </p>

        <button
          type="button"
          onClick={onClose}
          className="btn-3d mt-7 w-full rounded-[24px] border-[3px] border-red-line bg-flag-red-fill px-6 py-4 font-display text-[15px] font-black uppercase tracking-[0.18em] text-on-fill"
          style={
            {
              ["--shadow-color"]: "var(--color-red-line)",
            } as React.CSSProperties
          }
        >
          I&apos;m ready
        </button>
      </div>
    </div>,
    document.body,
  );
}
