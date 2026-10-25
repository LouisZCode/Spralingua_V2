// Tiny Meta Pixel event helper (MetaPixel.tsx owns the base code load).
//
// window.fbq exists only after fbevents.js arrives (afterInteractive) — every
// call here is a safe no-op before that, and analytics must never throw into
// app code, so the whole body is guarded. eventID (4th-arg options object) is
// what later lets Conversions API deduplicate server events against these.
// LEGAL-003 (A1): also a no-op without a granted consent flag — covers a
// revoke that happens while the snippet is already loaded.
import { consentGranted } from "@/lib/consent";

// P3 (2026-10-25): what the backend needs to report a purchase server-side
// (Meta Conversions API, payments/meta_capi.py). Read ONCE at checkout
// start and stamped into the Stripe session's metadata, so the webhook
// fires the server Purchase only for consented learners — the same
// LEGAL-003 gate the pixel itself obeys. _fbp/_fbc are Meta's own
// first-party cookies (browser id / ad-click id): they tie the server
// event to the browser Meta already saw, which is what makes the match.
// Never a PII payload: consent flag + two cookie strings, nothing else.
export type MetaTrackingContext = {
  consent: boolean;
  fbp?: string;
  fbc?: string;
};

export function metaTrackingContext(): MetaTrackingContext {
  if (typeof window === "undefined") return { consent: false };
  let fbp: string | undefined;
  let fbc: string | undefined;
  try {
    for (const part of document.cookie.split(";")) {
      const eq = part.indexOf("=");
      if (eq < 1) continue;
      const k = part.slice(0, eq).trim();
      const v = part.slice(eq + 1).trim();
      if (k === "_fbp") fbp = v;
      else if (k === "_fbc") fbc = v;
    }
  } catch {
    // ignore — tracking must never break checkout
  }
  return { consent: consentGranted(), fbp, fbc };
}

// Pre-consent events (a signup can beat the non-blocking banner) park
// here and drain when the pixel mounts under a granted flag; on decline
// they are simply never sent. Bounded like the Amplitude queue. Only
// MetaPixel (which renders exclusively under a granted flag) drains them.
const fbqQueue: Array<{
  event: string;
  params?: Record<string, unknown>;
  opts?: { eventID?: string };
}> = [];

export function trackFbq(
  event: string,
  params?: Record<string, unknown>,
  opts?: { eventID?: string },
): void {
  try {
    if (!consentGranted()) {
      if (fbqQueue.length < 20) fbqQueue.push({ event, params, opts });
      return;
    }
    const fbq = (
      window as unknown as {
        fbq?: (
          cmd: string,
          name: string,
          params?: Record<string, unknown>,
          opts?: { eventID?: string },
        ) => void;
      }
    ).fbq;
    fbq?.("track", event, params ?? {}, opts ?? {});
  } catch {
    // ignore — tracking must never break the app
  }
}

// The parked pre-consent events' drain point — see fbqQueue above.

/** Drain the pre-consent queue once the pixel's fbq stub exists.
 *
 * Called from MetaPixel's mount effect. The base snippet loads
 * `afterInteractive`, so the fbq stub may not exist on the first tick —
 * this retries briefly (~5s cap) and gives up silently either way; a
 * queued event that never drains was never consented to. */
export function flushQueuedFbq(attempt = 0): void {
  try {
    if (typeof window === "undefined" || fbqQueue.length === 0) return;
    const fbq = (
      window as unknown as {
        fbq?: (
          cmd: string,
          name: string,
          params?: Record<string, unknown>,
          opts?: { eventID?: string },
        ) => void;
      }
    ).fbq;
    if (!fbq) {
      if (attempt < 100) {
        window.setTimeout(() => flushQueuedFbq(attempt + 1), 50);
      }
      return;
    }
    const queue = fbqQueue.splice(0, fbqQueue.length);
    for (const queued of queue) {
      fbq("track", queued.event, queued.params ?? {}, queued.opts ?? {});
    }
  } catch {
    // ignore — tracking must never break the app
  }
}
