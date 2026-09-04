/**
 * OBS-013 — shared privacy scrub for every Sentry init in this app (client
 * + server). Belt-and-suspenders on top of `sendDefaultPii: false`: strips
 * any Authorization/Cookie header and any `user` object from an event
 * before it leaves the process, and strips Set-Cookie from responses too.
 *
 * No call site in this app calls `Sentry.setUser(...)` — this exists so an
 * event can never carry a bearer token or a session cookie even if a
 * future call site adds one without reading this file first.
 */
import type { ErrorEvent } from "@sentry/nextjs";

const SENSITIVE_HEADER_NAMES = new Set([
  "authorization",
  "cookie",
  "set-cookie",
]);

function scrubHeaders(headers: Record<string, unknown> | undefined) {
  if (!headers) return;
  for (const key of Object.keys(headers)) {
    if (SENSITIVE_HEADER_NAMES.has(key.toLowerCase())) {
      delete headers[key];
    }
  }
}

export function sentryBeforeSend(event: ErrorEvent): ErrorEvent {
  if (event.request) {
    scrubHeaders(event.request.headers as Record<string, unknown> | undefined);
    delete event.request.cookies;
  }
  if (event.contexts?.response) {
    scrubHeaders(
      (event.contexts.response as { headers?: Record<string, unknown> })
        .headers,
    );
  }
  delete event.user;
  return event;
}
