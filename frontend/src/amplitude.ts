"use client";

import * as amplitude from "@amplitude/unified";
import { useEffect } from "react";
import { consentGranted, useConsent } from "@/lib/consent";

const AMPLITUDE_API_KEY = process.env.NEXT_PUBLIC_AMPLITUDE_API_KEY;

// ANALYTICS-001 (2026-10-12): stable identity for the headless prod
// verifier. Every Playwright launch mints a fresh device cookie, so each
// run counted as a new unique user — ?probe=1 (see
// speedtest/.../env/_amplitude_prod_check.mjs) pins them all to one device.
const PROBE_DEVICE_ID = "probe-headless";
const isProbe =
  typeof window !== "undefined" &&
  new URLSearchParams(window.location.search).get("probe") === "1";

// LEGAL-003 (A1): nothing starts before consent. The <Amplitude /> component
// in layout.tsx calls enableAnalytics() once consent is granted; before that
// this module has no side effects at all. Revocation mid-session calls
// setOptOut(true) — already-queued events stop, future ones never send.
let initPromise: Promise<void> | null = null;
let pendingUserId: string | undefined;

function initAnalytics(): Promise<void> {
  if (initPromise) return initPromise;
  if (!AMPLITUDE_API_KEY) {
    console.warn("Amplitude API key missing — analytics disabled");
    return Promise.resolve();
  }
  initPromise = amplitude
    .initAll(AMPLITUDE_API_KEY, {
      analytics: { autocapture: true },
      sessionReplay: { sampleRate: 1 },
    })
    .then(() => {
      if (isProbe) amplitude.setDeviceId(PROBE_DEVICE_ID);
      // ANALYTICS-002 stitching may have identified the user before consent
      // arrived (sign-in earlier in the session) — apply it now.
      if (pendingUserId) amplitude.setUserId(pendingUserId);
      amplitude.track("Viewed Home Page", { prompt_version: "BA400.4" }); // helps improve this setup flow — safe to remove once you've verified the event lands
    });
  return initPromise;
}

export function enableAnalytics() {
  if (!consentGranted()) return;
  void initAnalytics().then(() => {
    try {
      amplitude.setOptOut(false); // re-grant after a revoke
    } catch {
      /* never break the app over tracking */
    }
  });
}

export function disableAnalytics() {
  try {
    amplitude.setOptOut(true);
  } catch {
    /* never break the app over tracking */
  }
}

// ANALYTICS-002 (2026-10-12): stitch browser events to the backend
// identity. Agents stamp users.id (the JWT subject, i.e. AuthUser.id) on
// every server-side event — setting the same string here merges a
// learner's devices and their backend turns into one Amplitude user.
// Probes never carry a real identity, even if one were available.
// LEGAL-003: remembered in pendingUserId so the stitch survives a consent
// grant that arrives AFTER sign-in (identify fires once, at sign-in).
export function identifyAnalyticsUser(userId: string) {
  if (typeof window === "undefined" || !AMPLITUDE_API_KEY || isProbe) return;
  pendingUserId = userId;
  if (consentGranted()) amplitude.setUserId(userId);
}

export function clearAnalyticsUser() {
  if (typeof window === "undefined" || !AMPLITUDE_API_KEY || isProbe) return;
  pendingUserId = undefined;
  amplitude.setUserId(undefined);
}

// P1 semantic events (2026-10-25): round-level product events fired from the
// practice surfaces — Mode Opened / Round Started / Round Completed / Voice
// Session Started / Voice Session Ended (see ARCHITECTURE.md's Marketing
// Analytics section for the taxonomy). Consent-gated by construction: the
// SDK only initializes after consent, so before a grant (or after a revoke)
// this is a no-op — the same LEGAL-003 promise the pixel obeys. Never
// throws, never blocks the caller; rides on the init promise so an event
// fired in the same tick as the grant still lands after the SDK is ready.
export function trackEvent(name: string, props?: Record<string, unknown>) {
  if (typeof window === "undefined" || !AMPLITUDE_API_KEY || isProbe) return;
  if (!consentGranted() || !initPromise) return;
  void initPromise.then(() => {
    try {
      amplitude.track(name, props);
    } catch {
      /* never break the app over tracking */
    }
  });
}

// Mounted in layout.tsx next to <MetaPixel />. Renders nothing; reacts to
// consent changes (banner / login notice / profile toggle all dispatch the
// same event through useConsent).
export function Amplitude() {
  const consent = useConsent();
  useEffect(() => {
    if (consent === "granted") enableAnalytics();
    else if (consent === "declined") disableAnalytics();
  }, [consent]);
  return null;
}

export default amplitude;
