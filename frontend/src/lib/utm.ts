// Signup attribution (2026-10-25): ad-click UTMs live only in the landing
// URL and die in the Google sign-in redirect, so we park them in
// localStorage at first load and read them back at the signup moment
// (StartCta fires them onto Sign Up Completed / CompleteRegistration).
//
// Local-only by design: writing here is not tracking — nothing leaves the
// browser, no third party is involved — so it runs before any consent
// choice. The data only ever transmits attached to a consent-gated event.
// Last-touch semantics: a landing WITH utm_* params overwrites the stored
// set; a landing without them (navigations, the redirect's return leg)
// keeps the previous set.

const UTM_KEYS = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_content",
  "utm_term",
] as const;

type UtmKey = (typeof UTM_KEYS)[number];

export const UTM_STORAGE_KEY = "utm";

export type UtmParams = Partial<Record<UtmKey, string>>;

/** Park the current URL's utm_* params (if any) in localStorage. */
export function captureUtm(): void {
  if (typeof window === "undefined") return;
  try {
    const params = new URLSearchParams(window.location.search);
    const utm: Record<string, string> = {};
    for (const key of UTM_KEYS) {
      const value = params.get(key);
      if (value) utm[key] = value;
    }
    if (Object.keys(utm).length) {
      window.localStorage.setItem(UTM_STORAGE_KEY, JSON.stringify(utm));
    }
  } catch {
    /* never break the app over attribution */
  }
}

/** Read the parked UTMs — {} when nothing was ever captured. */
export function readStoredUtm(): UtmParams {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(UTM_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return {};
    const out: UtmParams = {};
    for (const [key, value] of Object.entries(
      parsed as Record<string, unknown>,
    )) {
      if (
        (UTM_KEYS as readonly string[]).includes(key) &&
        typeof value === "string" &&
        value
      ) {
        out[key as UtmKey] = value;
      }
    }
    return out;
  } catch {
    return {};
  }
}
