import type { NextConfig } from "next";

// SEC-005 (Batch B/C): one place to derive every origin the security
// headers below need to name. Every value here is read from the SAME env
// vars the rest of the app already reads at build time (NEXT_PUBLIC_* is
// inlined into the client bundle, so this must track those exact reads or
// the CSP and the running app would silently disagree about the backend):
//
//   - NEXT_PUBLIC_API_URL -> frontend/src/lib/api.ts's HTTP_BASE / WS_BASE,
//     the single source of truth every fetch()/WebSocket() call in the app
//     already uses (ConversationView.tsx, AuthContext.tsx, lib/coins.ts,
//     HeroDemo.tsx's front-page demo socket, etc.) — this is the backend
//     origin connect-src must allow, in both its http(s) and ws(s) forms.
//   - NEXT_PUBLIC_SENTRY_DSN -> frontend/src/instrumentation-client.ts +
//     sentry.server.config.ts (OBS-013). When set, the browser SDK POSTs
//     error envelopes straight to the DSN's host — connect-src needs that
//     origin too. (frontend/Dockerfile doesn't yet ARG this var into the
//     build, so it's normally unset in prod today — deriving it here means
//     the CSP is already correct the day that gap closes, instead of a
//     second edit landing here later.)
//
// Google Identity Services (@react-oauth/google, used by
// SignInModal.tsx via Providers.tsx's <GoogleOAuthProvider>) injects its
// own <script src="https://accounts.google.com/gsi/client"> client-side
// and renders the sign-in button inside an accounts.google.com iframe —
// fixed origins, not env-derived, so they're written directly into the
// directives below rather than threaded through this helper. Nothing in
// this app calls the older gapi/apis.google.com library (grepped — only
// GSI is used), so apis.google.com is deliberately left out.
function deriveSecurityOrigins() {
  const rawApi = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8765";
  const apiHttp = rawApi.replace(/\/+$/, "");
  const apiWs = apiHttp.replace(/^http/, "ws");

  let sentryOrigin: string | null = null;
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (dsn) {
    try {
      sentryOrigin = new URL(dsn).origin;
    } catch {
      // Malformed DSN: Sentry.init() itself will no-op/log elsewhere: the
      // CSP just omits the ingest host rather than failing the build.
      sentryOrigin = null;
    }
  }

  return { apiHttp, apiWs, sentryOrigin };
}

const nextConfig: NextConfig = {
  // Emit a self-contained server build (.next/standalone) so the Docker runtime
  // image ships just the server + a trimmed node_modules instead of the full one.
  output: "standalone",

  // SEC-005 (Batch B/C): security headers, applied to every route — this is
  // one Next.js app with no separate public-asset origin to carve out.
  async headers() {
    const { apiHttp, apiWs, sentryOrigin } = deriveSecurityOrigins();

    const connectSrc = [
      "'self'",
      apiHttp,
      apiWs,
      "https://accounts.google.com",
      ...(sentryOrigin ? [sentryOrigin] : []),
    ].join(" ");

    // Content-Security-Policy-Report-Only, NOT enforced: violations are
    // logged to the browser console (and to a report endpoint, if one is
    // ever wired up) but nothing is blocked. A wrong directive here would
    // otherwise silently break Google sign-in, Sentry, fonts, or the
    // pipeline WebSocket — flip this to an enforced `Content-Security-Policy`
    // header only after watching real prod consoles for violations with
    // this in place for a while.
    //
    // 'unsafe-inline' on script-src is required by two inline scripts this
    // app ships intentionally: layout.tsx's DARK-001 pre-paint theme script
    // (`<script dangerouslySetInnerHTML>`, which must run before first
    // paint to avoid a light->dark flash) and Next.js's own inline
    // hydration bootstrap. A per-request nonce would remove the need for
    // it, but that needs a middleware.ts to mint the nonce and stamp it on
    // layout.tsx's script tag — this app has neither today, and frontend
    // edits for this change are scoped to next.config.* only (SEC-005), so
    // a nonce isn't feasible here; noted as a follow-up, not done.
    const csp = [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' https://accounts.google.com",
      "frame-src https://accounts.google.com",
      `connect-src ${connectSrc}`,
      "img-src 'self' data: https://lh3.googleusercontent.com",
      "style-src 'self' 'unsafe-inline'",
      "font-src 'self'",
      "media-src 'self' blob: data:",
      "worker-src 'self' blob:",
      "frame-ancestors 'none'",
    ].join("; ");

    return [
      {
        source: "/:path*",
        headers: [
          // Only meaningful once served over HTTPS (prod) — harmless on
          // local http:// dev, browsers simply ignore it there.
          {
            key: "Strict-Transport-Security",
            value: "max-age=31536000; includeSubDomains",
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
          // Enforced (unlike the CSP below): blocks this site from being
          // framed by any origin. frame-ancestors 'none' in the
          // Report-Only CSP above is the belt-and-suspenders version that
          // takes over once that header is flipped to enforced.
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          // The product records audio (ConversationView.tsx's mic capture)
          // — microphone stays available to this origin. Camera,
          // geolocation and payment are never used anywhere in the app.
          {
            key: "Permissions-Policy",
            value: "microphone=(self), camera=(), geolocation=(), payment=()",
          },
          { key: "Content-Security-Policy-Report-Only", value: csp },
        ],
      },
    ];
  },
};

export default nextConfig;
