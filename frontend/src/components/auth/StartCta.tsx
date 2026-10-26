"use client";

import { useState, type CSSProperties, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth, type SignInResult } from "./AuthContext";
import SignInModal from "./SignInModal";
import { trackFbq } from "@/lib/fbpixel";
import { setWelcomeGiftStatus } from "@/lib/welcomeGift";
import { trackEvent, setUserPropertiesOnce } from "@/amplitude";
import { readStoredUtm } from "@/lib/utm";

// Drop-in replacement for the landing page's "Start" <Link>s. Instead of
// navigating straight into a lesson, it gates on auth: already signed in → go
// to the /practice menu (choose conversation vs. vocabulary); otherwise open
// the Google sign-in modal and only advance once the session JWT is in hand.
// className/style/children are passed through so each call site keeps its
// existing look (nav link, hero button, closing button).
export default function StartCta({
  className,
  style,
  children,
}: {
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  const { token, ready, signInWithGoogle } = useAuth();
  const router = useRouter();
  const [showModal, setShowModal] = useState(false);

  function handleClick() {
    // Wait for localStorage hydration before deciding — otherwise a signed-in
    // user who clicks during the first render would wrongly see the modal.
    if (!ready) return;
    if (token) {
      router.push("/practice");
    } else {
      setShowModal(true);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        className={className}
        style={style}
      >
        {children}
      </button>
      {showModal && (
        <SignInModal
          signInWithGoogle={signInWithGoogle}
          onSuccess={(result: SignInResult) => {
            setShowModal(false);
            // Meta Pixel: registration only — returning logins carry
            // isNewUser=false and must not inflate the event. The
            // session-expired overlay never fires this (its onSuccess
            // ignores the result), so re-logins stay out by construction.
            if (result.isNewUser) {
              // PAY-002: queue the once-ever welcome-gift popup — the
              // 100-coin SIGNUP_GRANT already went to purchased_coins at
              // row creation; this only announces it. WelcomeGiftModal
              // stamps "done" on dismiss; the server flag can't fire
              // again, so no path re-shows it.
              setWelcomeGiftStatus("pending");
              // Signup attribution (2026-10-25): the utm_* params parked
              // at first landing (lib/utm.ts) ride the signup moment
              // everywhere — Amplitude event props, Amplitude user
              // properties (setOnce = first-touch, so every later event
              // including the server-side Coins Purchased stays
              // attributable without re-attaching anything), and the
              // Pixel's CompleteRegistration for Events Manager
              // readability. Both consent-gated legs queue if the
              // non-blocking banner is still unanswered and land the
              // moment it's accepted.
              const utm = readStoredUtm();
              // "Sign Up Attributed", NOT "Sign Up Completed": the server
              // (auth/routes.py) already fires Sign Up Completed
              // unconditionally with {method} — this client event is the
              // attribution record (UTMs), and a shared name would
              // double-count signups in every funnel.
              trackEvent("Sign Up Attributed", { ...utm });
              setUserPropertiesOnce(utm);
              trackFbq(
                "CompleteRegistration",
                utm,
                { eventID: `reg_${result.user.id}` },
              );
            }
            router.push("/practice");
          }}
          onClose={() => setShowModal(false)}
        />
      )}
    </>
  );
}
