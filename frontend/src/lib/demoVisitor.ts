// REL-001 follow-up (P2-IMPL): one anonymous visitor token per browser for
// the front-page demo (Luis's Proposal-2). Minted once with
// `crypto.randomUUID()` and persisted in `localStorage`, so repeat demo
// calls from the same browser can be recognised even though every tap still
// mints a fresh per-tab `demo-<uuid>` path id (HeroDemo.tsx) — that id keeps
// two tabs of one browser from colliding in the backend's `ACTIVE_TASKS`;
// this token is the thing that's actually stable across tabs and reloads.
//
// Every access is wrapped in try/catch: a private-mode browser or blocked
// storage must degrade to `null`, never break the demo itself — matches
// `shared/copy.ts`'s "a caught error becomes a graceful fallback, not a
// crash" convention used everywhere else in this codebase.

const VISITOR_KEY = "demo-visitor-v1";

export function getDemoVisitorId(): string | null {
  try {
    const existing = localStorage.getItem(VISITOR_KEY);
    if (existing) return existing;
    const minted = crypto.randomUUID();
    localStorage.setItem(VISITOR_KEY, minted);
    return minted;
  } catch {
    return null;
  }
}

// Read-only sibling for the sign-in link (AuthContext.tsx): a browser that
// never tapped the demo has no token, and must NOT be given one at sign-in
// — a freshly minted token that no demo session ever carried would link
// nothing and only fill `users.demo_visitor_id` with noise. Only the demo
// itself mints (getDemoVisitorId above).
export function peekDemoVisitorId(): string | null {
  try {
    return localStorage.getItem(VISITOR_KEY);
  } catch {
    return null;
  }
}

// PUT /auth/me/demo-visitor — first-wins server semantics (auth/routes.py).
// Resolves `true` when the server ANSWERED 2xx, whatever `linked` says: the
// caller only needs to know the request landed (so it can stop retrying);
// a `linked: false` (already claimed, by this account or another) is a
// final answer too. `false` means the request never landed (network error,
// non-2xx) and the caller may try again on a later sign-in. Never throws —
// this rides after sign-in, must never block or fail it.
export async function linkDemoVisitor(
  token: string,
  visitor: string
): Promise<boolean> {
  try {
    // Deferred import of HTTP_BASE avoided here on purpose — this module
    // has no other dependency on lib/api.ts's heavier surface, but the
    // constant itself is cheap and side-effect-free to import directly.
    const { HTTP_BASE } = await import("./api");
    // auth/routes.py mounts this under its "/auth" router prefix, same as
    // the sibling GET /auth/me and PUT /auth/level.
    const res = await fetch(`${HTTP_BASE}/auth/me/demo-visitor`, {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ visitor }),
    });
    return res.ok;
  } catch (err) {
    console.warn("[demoVisitor] link failed:", err);
    return false;
  }
}
