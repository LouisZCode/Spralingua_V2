"use client";

// Dev-only preview for the shared celebration util (lib/celebrate.ts).
// The NODE_ENV guard dead-code-eliminates in production builds — the page
// renders nothing there — so this never ships a live surface. Local preview:
// http://localhost:3000/celebrate-preview
// "soft" is what the welcome-gift popup fires (ONBOARD-004); "big" is the
// upgrade-success payoff (PAY-009).

import { celebrate } from "@/lib/celebrate";

const inkShadow = {
  boxShadow: "4px 4px 0 0 var(--color-line)",
} as const;

export default function CelebratePreview() {
  if (process.env.NODE_ENV === "production") return null;
  return (
    <main className="grid min-h-screen place-items-center bg-paper p-6">
      <div className="text-center">
        <h1 className="font-display text-[20px] font-black uppercase tracking-[0.04em] text-ink">
          Celebration preview
        </h1>
        <p className="mt-2 font-body text-[14px] text-ink-soft">
          Dev only — renders nothing in production builds.
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => celebrate("soft")}
            className="btn-3d rounded-[14px] border-[3px] border-line bg-gender-blue-fill px-6 py-2.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-on-fill"
            style={inkShadow}
          >
            Soft (welcome gift)
          </button>
          <button
            type="button"
            onClick={() => celebrate("big")}
            className="btn-3d rounded-[14px] border-[3px] border-line bg-flag-red-fill px-6 py-2.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-on-fill"
            style={inkShadow}
          >
            Big (upgrade!)
          </button>
        </div>
      </div>
    </main>
  );
}
