/**
 * OBS-013 — Next.js instrumentation hook. Loads the server-side Sentry
 * init (../sentry.server.config, itself gated on NEXT_PUBLIC_SENTRY_DSN)
 * once at boot, and wires onRequestError so a server-side rendering or
 * route-handler exception reaches Sentry the same way main.py's generic
 * `@app.exception_handler(Exception)` does on the backend.
 *
 * No edge runtime branch: this app has no middleware.ts and no route sets
 * `export const runtime = "edge"` (verified 2026-09-04 by grep across
 * src/) — add a sentry.edge.config.ts + edge branch here only alongside a
 * real edge route, not speculatively.
 */
import * as Sentry from "@sentry/nextjs";

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("../sentry.server.config");
  }
}

export const onRequestError = Sentry.captureRequestError;
