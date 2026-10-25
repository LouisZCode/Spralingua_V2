"use client";

import { GoogleOAuthProvider } from "@react-oauth/google";
import { useEffect, type ReactNode } from "react";
import ConsentGate from "@/components/ConsentGate";
import WelcomeGiftModal from "@/components/WelcomeGiftModal";
import { captureUtm } from "@/lib/utm";
import { AuthProvider } from "./AuthContext";

// Client-side root providers. GoogleOAuthProvider must wrap anything that
// renders <GoogleLogin>; AuthProvider holds our own session JWT + user. The
// clientId is the public browser-side OAuth client id (NEXT_PUBLIC_), not a
// secret. Empty string when unset — the Google button then no-ops, which is the
// expected local state until the id is pasted into frontend/.env.local.
export default function Providers({ children }: { children: ReactNode }) {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";
  // Signup attribution (2026-10-25): park the landing URL's utm_* params
  // on every first client load — before any navigation or the Google
  // sign-in redirect can wipe them (see lib/utm.ts). Local-only, no
  // network, so it needs no consent; the data only leaves the browser
  // attached to a later consent-gated event.
  useEffect(() => {
    captureUtm();
  }, []);
  return (
    <GoogleOAuthProvider clientId={clientId}>
      <AuthProvider>
        {/* PAY-002 welcome gift: once-ever signup popup, queued ahead of
            the consent notice — order is gift → consent → level picker. */}
        <WelcomeGiftModal />
        {/* LEGAL-003 (A1): consent banner + post-login notice. Inside
            AuthProvider because the notice waits for a signed-in user. */}
        <ConsentGate />
        {children}
      </AuthProvider>
    </GoogleOAuthProvider>
  );
}
