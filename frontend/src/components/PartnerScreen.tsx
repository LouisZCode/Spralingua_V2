"use client";

import { PARTNERS, type PartnerId } from "./shared/tandem";
import AppHeader from "@/components/shared/AppHeader";

// TAND-008: the choose-your-partner step — first screen of /tandem, before
// the topic picker. Same visual language as TopicScreen (which it hands off
// to): paper grid, ink borders, btn-3d cards. One card per registry entry,
// so partner #3 is a data edit in shared/tandem.ts.
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// Letter-avatar accents per partner — Lena gold (her chat-bubble world),
// Paul red. Pure display; personality lives in the backend YAMLs.
const AVATAR_BG: Record<PartnerId, string> = {
  lena: "bg-flag-gold-soft",
  paul: "bg-flag-red-soft",
};

export default function PartnerScreen({
  onPick,
}: {
  onPick: (partner: PartnerId) => void;
}) {
  return (
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      {/* APPHDR-001: shared header — teacher/tandem topic screens sit in
                the narrower container. */}
      <AppHeader back={{ href: "/practice", label: "← All modes" }} maxWidth="max-w-3xl" />

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-14">
        <div className="rise-in">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            Grammatik-Tandem
          </p>
          <h1 className="mt-3 font-display text-[clamp(28px,4.6vw,44px)] font-black leading-[1.03] tracking-tight text-ink">
            Who do you want to talk to?
          </h1>
          <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
            Two partners, two kinds of German. Both chat only in German, each
            remembers your own conversations with them.
          </p>
        </div>

        <div className="rise-in mt-9 grid gap-5 sm:grid-cols-2" style={{ animationDelay: "120ms" }}>
          {Object.values(PARTNERS).map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => onPick(p.id)}
              className="btn-3d flex flex-col items-start gap-4 rounded-3xl border-[3px] border-line bg-card px-6 py-6 text-left transition hover:bg-paper-warm"
              style={inkShadow}
            >
              <span
                className={`flex h-16 w-16 items-center justify-center rounded-full border-[3px] border-line font-display text-[28px] font-black text-ink ${AVATAR_BG[p.id]}`}
              >
                {p.name[0]}
              </span>
              <span>
                <span className="block font-display text-[24px] font-black leading-tight text-ink">
                  {p.name}
                </span>
                <span className="mt-1 block font-body text-[12px] font-bold uppercase tracking-[0.14em] text-ink-muted">
                  {p.vibe}
                </span>
              </span>
              <span className="font-body text-[14.5px] leading-relaxed text-ink-soft">
                {p.bio}
              </span>
              <span className="mt-auto inline-flex items-center gap-1.5 font-display text-[13px] font-black uppercase tracking-[0.14em] text-flag-red">
                Start a chat
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2.5}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="h-3.5 w-3.5"
                >
                  <path d="M5 12h14M13 6l6 6-6 6" />
                </svg>
              </span>
            </button>
          ))}
        </div>
      </main>
    </div>
  );
}
