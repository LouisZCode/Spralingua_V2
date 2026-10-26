"use client";

// PAY-002 welcome gift (Variant A): one-time popup for brand-new accounts,
// announcing the 100-coin SIGNUP_GRANT that upsert_user credited into
// purchased_coins at row creation. Shown while the welcome-gift-v1 flag
// reads "pending" (written by StartCta when the sign-in response carries
// isNewUser=true — server truth, so the moment exists exactly once ever);
// dismissal stamps "done". Mounted in Providers next to ConsentGate, which
// waits for this to clear before showing the legal notice — the agreed
// order is gift → consent → level picker.

import Image from "next/image";
import { useEffect } from "react";
import {
  setWelcomeGiftStatus,
  useWelcomeGift,
} from "@/lib/welcomeGift";
import { celebrate } from "@/lib/celebrate";

// House ink-shadow per-file constant (see ConsentGate convention).
const inkShadow = {
  boxShadow: "4px 4px 0 0 var(--color-line)",
} as const;

export default function WelcomeGiftModal() {
  const status = useWelcomeGift();
  const show = status === "pending";
  // ONBOARD-004 update: the gift arrives with the big celebration burst
  // (owner picked the big variant 2026-10-26 — "trigger it once"; the
  // welcome-gift-v1 flag guarantees exactly that). Shared util with
  // PAY-009; no audio by owner decision — see lib/celebrate.ts.
  useEffect(() => {
    if (show) celebrate("big");
  }, [show]);
  if (!show) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Welcome gift"
      className="fixed inset-0 z-[90] grid place-items-center bg-scrim/60 p-6 backdrop-blur-sm"
    >
      <div className="lift-panel w-full max-w-[420px] rounded-[28px] border-[3px] border-line bg-card px-6 pb-7 pt-6 text-center shadow-[8px_8px_0_0_var(--color-line)]">
        <Image
          src="/mascot/raven.png"
          alt="Spralingua raven mascot"
          width={64}
          height={64}
          priority
          className="mascot-keyline mx-auto h-16 w-16 select-none"
        />
        <h2 className="mt-3 font-display text-[22px] font-black uppercase leading-tight tracking-[0.04em] text-ink">
          Welcome to Spralingua!
        </h2>
        {/* The gift: the signup grant, as one gold coin badge */}
        <div className="mx-auto mt-4 flex h-24 w-24 flex-col items-center justify-center rounded-full border-[3px] border-line bg-flag-gold shadow-[4px_4px_0_0_var(--color-line)]">
          <span aria-hidden className="text-[20px] leading-none">
            🪙
          </span>
          <span className="font-display text-[28px] font-black leading-tight text-ink-fixed">
            100
          </span>
        </div>
        <p className="mx-auto mt-4 max-w-[300px] font-body text-[14px] leading-relaxed text-ink-soft">
          As a welcome gift, we&apos;re giving you 100 free coins to start
          your German journey!
        </p>
        <button
          type="button"
          autoFocus
          onClick={() => setWelcomeGiftStatus("done")}
          className="btn-3d mt-5 rounded-[14px] border-[3px] border-line bg-gender-blue-fill px-8 py-2.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-on-fill"
          style={inkShadow}
        >
          Let&apos;s go! ▸
        </button>
      </div>
    </div>
  );
}
