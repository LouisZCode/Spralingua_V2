"use client";

import { GoogleOAuthProvider } from "@react-oauth/google";
import { type ReactNode } from "react";
import { AuthProvider } from "./AuthContext";

// Client-side root providers. GoogleOAuthProvider must wrap anything that
// renders <GoogleLogin>; AuthProvider holds our own session JWT + user. The
// clientId is the public browser-side OAuth client id (NEXT_PUBLIC_), not a
// secret. Empty string when unset — the Google button then no-ops, which is the
// expected local state until the id is pasted into frontend/.env.local.
export default function Providers({ children }: { children: ReactNode }) {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";
  return (
    <GoogleOAuthProvider clientId={clientId}>
      <AuthProvider>{children}</AuthProvider>
    </GoogleOAuthProvider>
  );
}
