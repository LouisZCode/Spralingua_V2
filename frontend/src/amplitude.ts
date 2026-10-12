"use client";

import * as amplitude from "@amplitude/unified";

const AMPLITUDE_API_KEY = process.env.NEXT_PUBLIC_AMPLITUDE_API_KEY;

// ANALYTICS-001 (2026-10-12): stable identity for the headless prod
// verifier. Every Playwright launch mints a fresh device cookie, so each
// run counted as a new unique user — ?probe=1 (see
// speedtest/.../env/_amplitude_prod_check.mjs) pins them all to one device.
const PROBE_DEVICE_ID = "probe-headless";
const isProbe =
  typeof window !== "undefined" &&
  new URLSearchParams(window.location.search).get("probe") === "1";

if (typeof window !== "undefined") {
  if (!AMPLITUDE_API_KEY) {
    console.warn("Amplitude API key missing — analytics disabled");
  } else {
    // Track only after init resolves so the probe device ID (below) is
    // already in place before the first event is stamped.
    void amplitude
      .initAll(AMPLITUDE_API_KEY, {
        analytics: { autocapture: true },
        sessionReplay: { sampleRate: 1 },
      })
      .then(() => {
        if (isProbe) amplitude.setDeviceId(PROBE_DEVICE_ID);
        amplitude.track("Viewed Home Page", { prompt_version: "BA400.4" }); // helps improve this setup flow — safe to remove once you've verified the event lands
      });
  }
}

// ANALYTICS-002 (2026-10-12): stitch browser events to the backend
// identity. Agents stamp users.id (the JWT subject, i.e. AuthUser.id) on
// every server-side event — setting the same string here merges a
// learner's devices and their backend turns into one Amplitude user.
// Probes never carry a real identity, even if one were available.
export function identifyAnalyticsUser(userId: string) {
  if (typeof window === "undefined" || !AMPLITUDE_API_KEY || isProbe) return;
  amplitude.setUserId(userId);
}

export function clearAnalyticsUser() {
  if (typeof window === "undefined" || !AMPLITUDE_API_KEY || isProbe) return;
  amplitude.setUserId(undefined);
}

export const Amplitude = () => null;

export default amplitude;
