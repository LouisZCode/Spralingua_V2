"use client";

// LEGAL-003 (A1): the single consent flag behind every non-essential
// tracker (Meta Pixel + Amplitude). One flag, one storage key, one event —
// the banner, the post-login notice and the profile toggle all write it,
// MetaPixel.tsx / amplitude.ts / fbpixel.ts all read it.
//
// localStorage (not a cookie) on purpose: the flag itself is a strictly
// necessary UI preference under § 25(2) TDDDG — remembering that you said
// no is the only way not to re-ask on every page.

import { useSyncExternalStore } from "react";

export type ConsentValue = "granted" | "declined";

export const CONSENT_STORAGE_KEY = "spralingua-consent-v1";
const CONSENT_EVENT = "spralingua:consent-changed";

export function getConsent(): ConsentValue | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(CONSENT_STORAGE_KEY);
    return v === "granted" || v === "declined" ? v : null;
  } catch {
    return null; // Safari private mode / storage disabled
  }
}

export function consentGranted(): boolean {
  return getConsent() === "granted";
}

export function setConsent(value: ConsentValue): void {
  try {
    window.localStorage.setItem(CONSENT_STORAGE_KEY, value);
  } catch {
    // ignore — never break the app over tracking state
  }
  window.dispatchEvent(
    new CustomEvent<ConsentValue>(CONSENT_EVENT, { detail: value }),
  );
}

// React hook over the flag via useSyncExternalStore — no setState-in-effect,
// no hydration mismatch (server snapshot is null; the client syncs right
// after hydration). Banner/notice visibility is then pure derivation.
function subscribeConsent(callback: () => void) {
  window.addEventListener(CONSENT_EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(CONSENT_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

export function useConsent(): ConsentValue | null {
  return useSyncExternalStore(subscribeConsent, getConsent, () => null);
}
