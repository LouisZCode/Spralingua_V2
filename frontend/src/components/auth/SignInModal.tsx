"use client";

import { useState } from "react";
import { createPortal } from "react-dom";
import { GoogleLogin } from "@react-oauth/google";
import { useAuth } from "./AuthContext";

// Modal hosting Google's official <GoogleLogin> button. That component is the
// only web path that yields a Google *ID token* (cred.credential) — which is
// what our backend's /auth/google expects. (The useGoogleLogin hook returns an
// access token instead, the wrong shape for us.) onSuccess fires only after our
// own session JWT is minted and stored by signInWithGoogle.
export default function SignInModal({
  onSuccess,
  onClose,
}: {
  onSuccess: () => void;
  onClose: () => void;
}) {
  const { signInWithGoogle } = useAuth();
  const [error, setError] = useState<string | null>(null);

  // Portal to <body>: the landing page's CTA sits inside a `.rise-in` column
  // that animates `transform`, which makes `position: fixed` resolve against
  // that column instead of the viewport. Rendering outside it lets the overlay
  // truly cover the whole page.
  return createPortal(
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-ink/60 p-6 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-sm rounded-[28px] border-[3px] border-ink bg-white p-8 text-center shadow-[0_8px_0_var(--color-ink)]"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute right-4 top-4 grid h-8 w-8 place-items-center rounded-full border-[2px] border-ink text-ink transition-colors hover:bg-ink hover:text-white"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.5}
            strokeLinecap="round"
            className="h-4 w-4"
          >
            <path d="M6 6 L18 18 M18 6 L6 18" />
          </svg>
        </button>

        <h2 className="font-display text-[26px] font-black leading-tight text-ink">
          Sign in to start
        </h2>
        <p className="mx-auto mt-3 max-w-[260px] font-body text-[14px] leading-relaxed text-ink-soft">
          Sign in with Google to begin your lesson and save your progress.
        </p>

        <div className="mt-7 flex justify-center">
          <GoogleLogin
            onSuccess={async (cred) => {
              if (!cred.credential) {
                setError("No credential returned from Google.");
                return;
              }
              try {
                await signInWithGoogle(cred.credential);
                onSuccess();
              } catch {
                setError("Sign-in failed. Please try again.");
              }
            }}
            onError={() => setError("Google sign-in was cancelled or failed.")}
          />
        </div>

        {error && (
          <p className="mt-4 font-body text-[13px] font-semibold text-flag-red">
            {error}
          </p>
        )}

        {/* LEGAL-001: consent + age gate, right under the sign-in button. */}
        <p className="mx-auto mt-5 max-w-[280px] font-body text-[11px] leading-relaxed text-ink-muted">
          By continuing you confirm that you are 16 or older and agree to our{" "}
          <a
            href="/legal/terms"
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 hover:text-ink"
          >
            Terms of Service
          </a>{" "}
          and{" "}
          <a
            href="/legal/privacy"
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 hover:text-ink"
          >
            Privacy Policy
          </a>
          .
        </p>
      </div>
    </div>,
    document.body,
  );
}
