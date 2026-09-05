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

// @sentry/nextjs is never wrapped with withSentryConfig here (no tunnel
// route configured — grepped, confirmed 2026-09-05), so the browser SDK
// posts error envelopes straight to the DSN host derived above; nothing
// else to add for it. Its browser init (instrumentation-client.ts) pins
// tracesSampleRate/both replay sample rates to 0 and loads no replay
// integration, so it opens no extra worker or iframe either.
//
// @stripe/stripe-js / js.stripe.com are NOT loaded anywhere (grepped
// package.json + every payments-facing component): Pricing.tsx's checkout
// and billing-portal buttons only `fetch()` our own backend for a URL,
// then `window.location.assign(url)` — a full top-level navigation away
// from this origin, which script-src/connect-src/frame-src don't govern.

const nextConfig: NextConfig = {
  // Emit a self-contained server build (.next/standalone) so the Docker runtime
  // image ships just the server + a trimmed node_modules instead of the full one.
  output: "standalone",

  // SEC-005 (Batch B/C): security headers, applied to every route — this is
  // one Next.js app with no separate public-asset origin to carve out.
  async headers() {
    const { apiHttp, apiWs, sentryOrigin } = deriveSecurityOrigins();
    const isProd = process.env.NODE_ENV === "production";

    const connectSrc = [
      "'self'",
      apiHttp,
      apiWs,
      "https://accounts.google.com",
      // No daily.co origin here, on purpose (SEC-005, 2026-09-05):
      // @pipecat-ai/websocket-transport's DEFAULT media manager is Daily's
      // own `DailyMediaManager`, which fetches and eval()s a call-object
      // bundle from c.daily.co on every voice connect just to enumerate
      // devices — the first enforced build of this header hung every voice
      // surface on "Connecting…" because of it. Both voice surfaces
      // (ConversationView.tsx, HeroDemo.tsx) now pass an explicit
      // `mediaManager: new WavMediaManager(...)` (pure Web Audio, exported
      // by the same package), so nothing on the page ever talks to Daily,
      // no 'unsafe-eval' is needed, and the learner's IP never reaches a
      // processor the privacy page does not name. Keep it that way: a
      // transport constructed WITHOUT `mediaManager` will fail under this
      // header, loudly, which is the intended tripwire.
      ...(sentryOrigin ? [sentryOrigin] : []),
    ].join(" ");

    // CSP-002 (2026-09-05): flipped from report-only to enforced for
    // production builds (`next build` / `next start`, both of which set
    // NODE_ENV=production themselves) — prod is live today. `next dev`
    // (NODE_ENV=development) keeps the Report-Only header below regardless:
    // it's never customer-facing, and Turbopack/webpack's dev-only runtime
    // behavior (HMR, source maps) isn't worth chasing into an enforced
    // policy that buys nothing there.
    //
    // Every directive below was re-verified against the actual app before
    // enforcing (grepped frontend/src + package.json + the two
    // @pipecat-ai/* dist bundles, 2026-09-05):
    //
    // - script-src: 'unsafe-inline' is required by two inline scripts this
    //   app ships intentionally — layout.tsx's DARK-001 pre-paint theme
    //   script (`<script dangerouslySetInnerHTML>`, must run before first
    //   paint to avoid a light->dark flash) and Next.js's own inline
    //   hydration bootstrap. A per-request nonce would remove the need for
    //   it, but that needs a middleware.ts to mint+stamp one — this app has
    //   neither, and frontend edits for this change are scoped to
    //   next.config.* only, so a nonce isn't feasible here; noted as a
    //   follow-up, not done. No 'unsafe-eval' and no daily.co origin — see
    //   the connect-src comment above for why the transport is built with
    //   an explicit WavMediaManager. `blob:` is required too, and is NOT
    //   optional:
    //   @pipecat-ai/websocket-transport's WavStreamPlayer (bot audio
    //   playback) and WavRecorder (mic capture) each build their
    //   AudioWorklet processor as a `new Blob([...], {type:
    //   "application/javascript"})` and load it via
    //   `audioWorklet.addModule(URL.createObjectURL(blob))` (dist/index.js,
    //   both the `stream_processor` and `audio_processor` worklets) —
    //   browsers check that load against script-src (not worker-src,
    //   despite the name), so every voice surface (Clara, tandem,
    //   conversation, respond, and the front-page demo socket, since all of
    //   them go through ConversationView.tsx / HeroDemo.tsx's
    //   WebSocketTransport) would go silent under an enforced CSP without
    //   it — verified live in the production-build check below.
    // - frame-src: the Google Identity Services sign-in button
    //   (@react-oauth/google's <GoogleOAuthProvider>, SignInModal.tsx)
    //   renders inside an https://accounts.google.com iframe.
    // - connect-src: 'self' for our own API routes, the derived backend
    //   origin (http+ws forms, covers the WS lesson/tandem/teacher sockets
    //   AND the unauthenticated /ws/demo/{user_id} socket — all go through
    //   the same NEXT_PUBLIC_API_URL-derived base in lib/api.ts),
    //   accounts.google.com for the GIS token exchange XHR, and the Sentry
    //   DSN host when one is configured (no tunnel route is set up, so the
    //   browser SDK posts straight to it).
    // - img-src: 'self' + data: (the theme mascot art plus small inline
    //   data URIs) + lh3.googleusercontent.com, the Google profile-picture
    //   host every avatar (<img>/next/image unoptimized) points at
    //   directly — grepped, no other Google image host is used and the
    //   older gapi/apis.google.com library is not loaded anywhere.
    // - style-src: 'unsafe-inline' for React's `style={{...}}` inline
    //   style attributes (58 call sites) — no OTHER external stylesheet
    //   host is ever loaded (next/font self-hosts, no Google Fonts
    //   <link>). https://accounts.google.com IS required here though —
    //   confirmed live against the enforced header, 2026-09-05: the GIS
    //   button injects `<link rel="stylesheet"
    //   href="https://accounts.google.com/gsi/style">` into our OWN
    //   document head (not inside its iframe), which style-src blocks
    //   without this origin — SignInModal.tsx's sign-in button silently
    //   loses its styling otherwise.
    // - font-src 'self': next/font/google self-hosts every face at build
    //   time; nothing fetches fonts.googleapis.com/fonts.gstatic.com.
    // - media-src: 'self' + blob: + data: for the silent-WAV <audio> primer
    //   (ConversationView.tsx / HeroDemo.tsx use a `data:audio/wav;...`
    //   src to satisfy iOS's autoplay-unlock gesture) and any blob audio.
    // - worker-src: 'self' + blob: kept as the belt-and-suspenders home for
    //   the same worklet blob: URLs in case a browser instead enforces
    //   worklets under this directive (grepped: no dedicated `new
    //   Worker(...)` exists anywhere in this app's own code or its two
    //   @pipecat-ai/* dependencies today, only the worklets above).
    // - object-src 'none': no <object>/<embed>/<applet> anywhere in the app
    //   — blocks legacy plugin content outright.
    // - base-uri 'self': no <base> tag is rendered; stops a successful
    //   injection from rewriting every relative URL on the page.
    // - form-action 'self': every <form> in the app (Genus, Verbindungen,
    //   Zeitfärbung, Fälle, Bauteil trainers, satzschmiede/AddWordForm)
    //   uses `onSubmit` + fetch, none post cross-origin; Stripe
    //   checkout/portal are JS redirects (`window.location.assign`), never
    //   an HTML form submission, so they're unaffected by this directive.
    // - frame-ancestors 'none': unchanged, now doubly enforced alongside
    //   the always-on X-Frame-Options: DENY below.
    // - upgrade-insecure-requests: only added once the derived backend
    //   origin is itself https — added unconditionally it would break the
    //   http://localhost backend this exact header is verified against in
    //   a local production build, and it buys nothing extra when the app
    //   is already all-https in real prod.
    const directives = [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' blob: https://accounts.google.com",
      "frame-src https://accounts.google.com",
      `connect-src ${connectSrc}`,
      "img-src 'self' data: https://lh3.googleusercontent.com",
      "style-src 'self' 'unsafe-inline' https://accounts.google.com",
      "font-src 'self'",
      "media-src 'self' blob: data:",
      "worker-src 'self' blob:",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "frame-ancestors 'none'",
    ];
    if (isProd && apiHttp.startsWith("https://")) {
      directives.push("upgrade-insecure-requests");
    }
    const csp = directives.join("; ");

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
          // Enforced independently of the CSP below: blocks this site from
          // being framed by any origin. frame-ancestors 'none' in the CSP
          // is the belt-and-suspenders version for browsers that honor it.
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          // The product records audio (ConversationView.tsx's mic capture)
          // — microphone stays available to this origin. Camera,
          // geolocation and payment are never used anywhere in the app.
          {
            key: "Permissions-Policy",
            value: "microphone=(self), camera=(), geolocation=(), payment=()",
          },
          {
            key: isProd
              ? "Content-Security-Policy"
              : "Content-Security-Policy-Report-Only",
            value: csp,
          },
        ],
      },
    ];
  },
};

export default nextConfig;
