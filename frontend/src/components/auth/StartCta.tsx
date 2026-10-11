"use client";

import { useState, type CSSProperties, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth, type SignInResult } from "./AuthContext";
import SignInModal from "./SignInModal";
import { trackFbq } from "@/lib/fbpixel";

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
              trackFbq(
                "CompleteRegistration",
                {},
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
