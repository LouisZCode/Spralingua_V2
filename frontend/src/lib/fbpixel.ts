// Tiny Meta Pixel event helper (MetaPixel.tsx owns the base code load).
//
// window.fbq exists only after fbevents.js arrives (afterInteractive) — every
// call here is a safe no-op before that, and analytics must never throw into
// app code, so the whole body is guarded. eventID (4th-arg options object) is
// what later lets Conversions API deduplicate server events against these.
// LEGAL-003 (A1): also a no-op without a granted consent flag — covers a
// revoke that happens while the snippet is already loaded.
import { consentGranted } from "@/lib/consent";

export function trackFbq(
  event: string,
  params?: Record<string, unknown>,
  opts?: { eventID?: string },
): void {
  try {
    if (!consentGranted()) return;
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
