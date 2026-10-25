"use client";

// PAY-002 welcome gift: the once-ever signup-gift popup.
//
// The backend's `is_new_user` (the xmax == 0 trick in upsert_user) is true
// on the row's FIRST creation only, so it is server truth that a welcome
// moment exists exactly once per account. StartCta writes "pending" when
// the sign-in response carries it; WelcomeGiftModal (mounted in Providers)
// shows while "pending" and stamps "done" on dismiss.
//
// localStorage is only the show-once guard, not the trigger — it covers the
// reload between the sign-in response and the render. Token-replay sessions
// never call /auth/google, so returning logins can't re-show it.
//
// ConsentGate and PracticeMenu's level picker wait for this flag to leave
// "pending": the agreed order is gift → consent notice → level picker.

import { useSyncExternalStore } from "react";

export type WelcomeGiftStatus = "pending" | "done";

export const WELCOME_GIFT_STORAGE_KEY = "welcome-gift-v1";
const WELCOME_GIFT_EVENT = "spralingua:welcome-gift-changed";

export function getWelcomeGiftStatus(): WelcomeGiftStatus | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(WELCOME_GIFT_STORAGE_KEY);
    return v === "pending" || v === "done" ? v : null;
  } catch {
    return null; // Safari private mode / storage disabled
  }
}

export function setWelcomeGiftStatus(value: WelcomeGiftStatus): void {
  try {
    window.localStorage.setItem(WELCOME_GIFT_STORAGE_KEY, value);
  } catch {
    // ignore — never break the app over popup state
  }
  window.dispatchEvent(
    new CustomEvent<WelcomeGiftStatus>(WELCOME_GIFT_EVENT, { detail: value })
  );
}

function subscribeWelcomeGift(callback: () => void) {
  window.addEventListener(WELCOME_GIFT_EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(WELCOME_GIFT_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

export function useWelcomeGift(): WelcomeGiftStatus | null {
  return useSyncExternalStore(
    subscribeWelcomeGift,
    getWelcomeGiftStatus,
    () => null
  );
}
