"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { HTTP_BASE } from "@/lib/api";

// localStorage key holding { token, user }. The session JWT is replayed on the
// WS handshake (?token=) and on /say (Authorization: Bearer) — see AUTH-001.
const STORAGE_KEY = "spralingua_auth";

export type AuthUser = {
  id: string;
  email: string | null;
  name: string | null;
  picture: string | null;
  role: string;
};

type AuthState = {
  token: string | null;
  user: AuthUser | null;
  // True once the one-time localStorage hydration has run. Guards must wait for
  // this before deciding "not signed in" — otherwise a page refresh would
  // bounce an authenticated user on the first render.
  ready: boolean;
  signInWithGoogle: (credential: string) => Promise<void>;
  signOut: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed?.token && parsed?.user) {
          // SSR-safe hydration: localStorage is client-only, so we set auth
          // state on mount; reading it during render would mismatch server HTML.
          // eslint-disable-next-line react-hooks/set-state-in-effect
          setToken(parsed.token);
          setUser(parsed.user);
        }
      }
    } catch {
      // Malformed storage — treat as signed out.
    }
    setReady(true);
  }, []);

  async function signInWithGoogle(credential: string) {
    const res = await fetch(`${HTTP_BASE}/auth/google`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential }),
    });
    if (!res.ok) {
      throw new Error(`Sign-in failed (${res.status})`);
    }
    const data = (await res.json()) as { token: string; user: AuthUser };
    setToken(data.token);
    setUser(data.user);
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ token: data.token, user: data.user })
    );
  }

  function signOut() {
    setToken(null);
    setUser(null);
    localStorage.removeItem(STORAGE_KEY);
    // Shared-browser hygiene (CHORE-001): don't leave the dev-unlock flag set
    // for whoever signs in next on this device.
    localStorage.removeItem("spralingua_dev_unlocked");
  }

  return (
    <AuthContext.Provider
      value={{ token, user, ready, signInWithGoogle, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within <AuthProvider>");
  }
  return ctx;
}
