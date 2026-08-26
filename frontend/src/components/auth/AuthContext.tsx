"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { HTTP_BASE } from "@/lib/api";

// localStorage key holding { token, user }. The session JWT is replayed on the
// WS handshake (?token=) and on /say (Authorization: Bearer) — see AUTH-001.
const STORAGE_KEY = "spralingua_auth";

// LEVEL-001/LEVEL-002: four buckets, not six — a self-declared level is only
// accurate to about this resolution. B1 and B2+ are split even though the
// grammar taxonomy tops out at B1, because content-leveled exercises
// (Briefkasten seeds, Szenario question tiers) need the finer distinction.
export type Level = "A1" | "A2" | "B1" | "B2+";

export type AuthUser = {
  id: string;
  email: string | null;
  name: string | null;
  picture: string | null;
  role: string;
  // null = never asked. The picker shows on /practice until it's answered,
  // and until then the learner is served every item, as before LEVEL-001.
  level: Level | null;
  // PAY-001: billing tier — "free" | "basic" | "premium". Always present on
  // sign-in and /auth/me responses (the backend defaults it to "free"); the
  // localStorage hydration path below backfills it for sessions stored
  // before this field existed.
  tier: string;
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
  // PAY-001: re-pull the signed-in user from /auth/me and mirror it into
  // localStorage. Needed after a Stripe Checkout/portal round trip, where the
  // tier changes server-side (via webhook) without any local action of ours
  // to react to — the /pricing/success poller is the first caller. Returns
  // the fresh user (or null on failure) so a caller can inspect the result
  // immediately, without waiting on the next render.
  refreshUser: () => Promise<AuthUser | null>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);
  // PAY-002: guard so the timezone report fires once per browser session.
  const tzSentRef = useRef(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed?.token && parsed?.user) {
          // SSR-safe hydration: localStorage is client-only, so we set auth
          // state on mount; reading it during render would mismatch server HTML.
          setToken(parsed.token);
          // PAY-001: a session stored before the tier field existed has none
          // in localStorage — default it to "free" rather than leaving it
          // undefined, since callers (e.g. the /pricing page) trust it to
          // always be a string.
          setUser({ ...parsed.user, tier: parsed.user.tier ?? "free" });
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

  // PAY-001: see the AuthState docstring above. Memoized on `token` alone so
  // it's a stable reference across the setUser-triggered re-renders it
  // itself causes — a plain function here would give a poller keying an
  // effect off this callback a new identity every 2s and never converge.
  // PAY-002: one-time timezone report after sign-in or /auth/me hydration.
  // Fire-and-forget, never blocks or breaks sign-in.
  useEffect(() => {
    if (!token || !user) return;
    if (tzSentRef.current) return;
    try {
      if (sessionStorage.getItem("spralingua_tz_sent") === "1") {
        tzSentRef.current = true;
        return;
      }
    } catch {
      // storage unavailable — still try once per mount
    }
    tzSentRef.current = true;
    try {
      sessionStorage.setItem("spralingua_tz_sent", "1");
    } catch {
      // ignore
    }
    let tz: string;
    try {
      tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch {
      return;
    }
    if (!tz) return;
    import("@/lib/coins").then(({ putTimezone }) =>
      putTimezone(token, tz).catch(() => {})
    );
  }, [token, user]);

  const refreshUser = useCallback(async (): Promise<AuthUser | null> => {
    if (!token) return null;
    try {
      const res = await fetch(`${HTTP_BASE}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return null;
      const updated = (await res.json()) as AuthUser;
      setUser(updated);
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ token, user: updated })
      );
      return updated;
    } catch {
      return null;
    }
  }, [token]);

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
      value={{
        token,
        user,
        ready,
        signInWithGoogle,
        signOut,
        setLevel,
        refreshUser,
      }}
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
