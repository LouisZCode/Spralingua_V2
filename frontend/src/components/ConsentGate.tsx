"use client";

// LEGAL-003 (A1): the two consent surfaces.
//
// 1. First-visit banner (anonymous visitors): one line + Accept / Decline.
//    Without it, ad traffic landing signed-out would never be asked and
//    § 25 TDDDG would have no lawful anonymous tracking at all (A1-lite).
// 2. One-time post-login notice (Luis's specified copy, verbatim): shown
//    the moment a user signs in while no choice exists yet. "Continue"
//    records consent; a subdued "Decline" link keeps the choice real —
//    consent needs a genuine no-option to be consent.
//
// Both write the same spralingua-consent-v1 flag; the profile toggle is
// the third (reversible) surface. Mounted once inside Providers so it can
// read the auth state.

import { useAuth } from "@/components/auth/AuthContext";
import { setConsent, useConsent } from "@/lib/consent";
import { useWelcomeGift } from "@/lib/welcomeGift";
import Link from "next/link";

// House ink-shadow per-file constant (see profile/page.tsx convention).
const inkShadow = {
  boxShadow: "4px 4px 0 0 var(--color-line)",
} as const;

export default function ConsentGate() {
  const consent = useConsent();
  const { user } = useAuth();

  // All visibility is derived — no state, no "shown once" bookkeeping:
  // a recorded choice (either way) collapses both surfaces to null, which
  // IS the once-per-browser semantics. Signed in + no choice -> notice;
  // signed out + no choice -> banner. A banner choice made before sign-in
  // means the notice never appears.
  // PAY-002 welcome gift: the notice queues behind it — agreed order is
  // gift → consent → level picker. `useWelcomeGift` subscribes to the same
  // store the modal writes, so the notice appears the moment the gift is
  // dismissed (or never, if the gift already consumed the moment).
  const welcome = useWelcomeGift();
  const showNotice = !!user && consent === null && welcome !== "pending";
  const showBanner = !user && consent === null;
  if (!showNotice && !showBanner) return null;

  const grant = () => setConsent("granted");
  const decline = () => setConsent("declined");

  return (
    <>
      {showBanner && (
        <div
          role="region"
          aria-label="Tracking consent"
          className="fixed inset-x-0 bottom-0 z-40 px-4 pb-4"
        >
          <div className="mx-auto flex w-full max-w-[560px] flex-col gap-3 rounded-[22px] border-[3px] border-line bg-card px-5 py-4 shadow-[6px_6px_0_0_var(--color-line)]">
            <p className="font-body text-[13px] leading-relaxed text-ink-soft">
              We use cookies, webpage use and analytics and advertising
              measurement to improve the experience of our students on the
              webpage.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <button
                type="button"
                onClick={grant}
                className="btn-3d rounded-[14px] border-[3px] border-line bg-gender-blue-fill px-5 py-2 font-display text-[11px] font-black uppercase tracking-[0.14em] text-on-fill"
                style={inkShadow}
              >
                Accept
              </button>
              <button
                type="button"
                onClick={decline}
                className="btn-3d rounded-[14px] border-[3px] border-line bg-card px-5 py-2 font-display text-[11px] font-black uppercase tracking-[0.14em] text-ink"
                style={inkShadow}
              >
                Decline
              </button>
              <Link
                href="/legal/privacy"
                className="font-body text-[11px] font-bold uppercase tracking-[0.18em] text-ink-faint underline-offset-4 hover:text-ink hover:underline"
              >
                Privacy →
              </Link>
            </div>
          </div>
        </div>
      )}

      {showNotice && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Tracking notice"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
        >
          <div className="w-full max-w-[440px] rounded-[28px] border-[3px] border-line bg-card px-6 py-6 shadow-[8px_8px_0_0_var(--color-line)]">
            <p className="font-body text-[14px] leading-relaxed text-ink-soft">
              We track some website use to improve your experience — change
              it anytime in your profile.
            </p>
            <div className="mt-5 flex flex-col items-center gap-2">
              <button
                type="button"
                autoFocus
                onClick={grant}
                className="btn-3d rounded-[14px] border-[3px] border-line bg-gender-blue-fill px-6 py-2.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-on-fill"
                style={inkShadow}
              >
                Continue
              </button>
              <button
                type="button"
                onClick={decline}
                className="font-body text-[12px] font-bold text-ink-faint underline underline-offset-4 hover:text-ink"
              >
                Decline tracking
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
