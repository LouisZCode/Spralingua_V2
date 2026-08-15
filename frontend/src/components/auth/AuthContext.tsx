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

// LEVEL-001: three buckets, not six — a self-declared level is only accurate
// to about this resolution, and the grammar taxonomy tops out at B1.
export type Level = "A1" | "A2" | "B1+";

export type AuthUser = {
  id: string;
  email: string | null;
  name: string | null;
  picture: string | null;
  role: string;
  // null = never asked. The picker shows on /practice until it's answered,
  // and until then the learner is served every item, as before LEVEL-001.
  level: Level | null;
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
  setLevel: (level: Level | null) => Promise<void>;
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

  async function setLevel(level: Level | null) {
    if (!token) return;
    const res = await fetch(`${HTTP_BASE}/auth/level`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ level }),
    });
    if (!res.ok) {
      throw new Error(`Could not save your level (${res.status})`);
    }
    const updated = (await res.json()) as AuthUser;
    setUser(updated);
    // Mirror into localStorage so a refresh doesn't re-ask a question the
    // learner already answered.
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ token, user: updated })
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
      value={{ token, user, ready, signInWithGoogle, signOut, setLevel }}
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
