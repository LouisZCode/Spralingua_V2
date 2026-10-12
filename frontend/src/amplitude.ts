"use client";

import * as amplitude from "@amplitude/unified";

const AMPLITUDE_API_KEY = process.env.NEXT_PUBLIC_AMPLITUDE_API_KEY;

if (typeof window !== "undefined") {
  if (!AMPLITUDE_API_KEY) {
    console.warn("Amplitude API key missing — analytics disabled");
  } else {
    amplitude.initAll(AMPLITUDE_API_KEY, {
      analytics: { autocapture: true },
      sessionReplay: { sampleRate: 1 },
    });
    amplitude.track("Viewed Home Page", { prompt_version: "BA400.4" }); // helps improve this setup flow — safe to remove once you've verified the event lands
  }
}

export const Amplitude = () => null;

export default amplitude;
