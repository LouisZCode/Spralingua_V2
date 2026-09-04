/**
 * OBS-013 — Sentry client init, inert unless NEXT_PUBLIC_SENTRY_DSN is set.
 *
 * NEXT_PUBLIC_* vars are inlined at BUILD time (frontend/.env.example),
 * so this DSN must be present before `npm run build` — or as a Docker
 * build ARG (frontend/Dockerfile currently declares ARGs for
 * NEXT_PUBLIC_API_URL and NEXT_PUBLIC_GOOGLE_CLIENT_ID only; a
 * NEXT_PUBLIC_SENTRY_DSN Railway variable does nothing until the
 * Dockerfile also ARGs/ENVs it — see this feature's Solution note) — to
 * ever fire. Setting it only in Railway's runtime env does nothing for a
 * client bundle already built without it.
 *
 * Mirrors main.py's sentry_sdk.init(...) on the backend (OBS-013): errors
 * only (tracesSampleRate: 0 — Langfuse already owns latency),
 * sendDefaultPii: false, no session replay integration loaded (both replay
 * sample rates pinned to 0 as a second guard). See sentry.privacy.ts for
 * the beforeSend scrub (Authorization/Cookie headers, any user object) —
 * no call site here or anywhere in this app calls Sentry.setUser(...).
 *
 * Privacy page: frontend/src/app/legal/privacy/page.tsx (:117-119, :391)
 * currently promises "no analytics, tracking, or advertising technology of
 * any kind... not our own, and not from any third party." That page needs
 * one added sentence naming this error processor before
 * NEXT_PUBLIC_SENTRY_DSN is ever set on a production build — not this
 * file's job to edit that page (see todo_list.md::OBS-013's Gotchas).
 */
import * as Sentry from "@sentry/nextjs";
import { sentryBeforeSend } from "../sentry.privacy";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || "development",
    release: process.env.NEXT_PUBLIC_RAILWAY_GIT_COMMIT_SHA || undefined,
    sendDefaultPii: false,
    tracesSampleRate: 0,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
    beforeSend: sentryBeforeSend,
  });
}

// Required export (Next.js App Router navigation instrumentation hook) —
// Sentry.captureRouterTransitionStart is a safe no-op when Sentry.init(...)
// was never called above, same as onRequestError in src/instrumentation.ts.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
