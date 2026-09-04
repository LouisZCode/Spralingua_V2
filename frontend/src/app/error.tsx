"use client";

/**
 * UI-016 — route-segment error boundary. Next.js mounts this whenever a
 * render error is thrown anywhere below the root layout; layout.tsx and its
 * <Providers> stay mounted (unlike global-error.tsx, OBS-013, which only
 * fires when the root layout itself throws and has to replace <html>/
 * <body>), so AppHeader/useAuth are safe to use here. Must be a client
 * component — App Router convention, unlike most other files under app/.
 *
 * Reports the same way global-error.tsx does: Sentry.captureException(...)
 * is a safe no-op when NEXT_PUBLIC_SENTRY_DSN is unset (instrumentation-
 * client.ts never calls Sentry.init(...) in that case), so no extra gating
 * is needed here either.
 */
import { useEffect } from "react";
import Link from "next/link";
import * as Sentry from "@sentry/nextjs";
import { useAuth } from "@/components/auth/AuthContext";
import AppHeader from "@/components/shared/AppHeader";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  // Same "does the back link strand a signed-out visitor" rule Pricing.tsx
  // already uses (PAYHUB-001): a token means /practice is safe, otherwise
  // fall back to the public landing page.
  const { token } = useAuth();
  const signedIn = !!token;
  const backHref = signedIn ? "/practice" : "/";

  return (
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <AppHeader
        sticky={false}
        maxWidth="max-w-3xl"
        back={{ href: backHref, label: "← Back" }}
        right={
          <button
            type="button"
            onClick={reset}
            className="shrink-0 whitespace-nowrap font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
          >
            Try again
          </button>
        }
      />

      <main className="relative mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center px-6 py-16 text-center">
        <h1 className="font-display text-[26px] font-black leading-tight text-ink">
          Something broke
        </h1>
        <p className="mt-3 font-body text-[15px] leading-relaxed text-ink-soft">
          This screen hit a snag on our end. Try again, or head back — your
          place is right where you left it.
        </p>
        <Link
          href={backHref}
          className="btn-3d mt-8 inline-flex items-center justify-center rounded-[24px] border-[3px] border-line bg-card px-7 py-4 font-display text-[15px] font-black uppercase tracking-[0.16em] text-ink"
          style={{ ["--shadow-color"]: "var(--color-line)" } as React.CSSProperties}
        >
          {signedIn ? "Back to practice" : "Back home"} →
        </Link>
      </main>
    </div>
  );
}
