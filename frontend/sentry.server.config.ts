/**
 * OBS-013 — Sentry server init. Inert unless NEXT_PUBLIC_SENTRY_DSN is set
 * (same gate the client uses, see instrumentation-client.ts — a single env
 * var name, read here at server runtime rather than inlined at build).
 * Loaded once at boot by src/instrumentation.ts's register().
 *
 * Mirrors main.py's sentry_sdk.init(...) on the backend (OBS-013): errors
 * only (tracesSampleRate: 0 — Langfuse already owns latency),
 * sendDefaultPii: false, plus the same beforeSend scrub the client uses
 * (sentry.privacy.ts). `release` reads Railway's auto-injected
 * RAILWAY_GIT_COMMIT_SHA directly — this runs server-side at container
 * runtime, not inlined at build, so no Dockerfile/ARG change is needed for
 * this half (unlike the client bundle — see instrumentation-client.ts).
 */
import * as Sentry from "@sentry/nextjs";
import { sentryBeforeSend } from "./sentry.privacy";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || "development",
    release:
      process.env.RAILWAY_GIT_COMMIT_SHA ||
      process.env.NEXT_PUBLIC_RAILWAY_GIT_COMMIT_SHA ||
      undefined,
    sendDefaultPii: false,
    tracesSampleRate: 0,
    beforeSend: sentryBeforeSend,
  });
}
