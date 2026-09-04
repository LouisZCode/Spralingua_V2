"use client";

/**
 * OBS-013 — last-resort React render-error boundary at the root. Next.js
 * only invokes this when the root layout (layout.tsx) itself throws, and
 * it must render its own <html>/<body> because it replaces layout.tsx when
 * it fires — this is not a general error page, and does not touch
 * layout.tsx or any other route's error handling.
 *
 * Sentry.captureException(...) is a safe no-op when NEXT_PUBLIC_SENTRY_DSN
 * is unset (instrumentation-client.ts never calls Sentry.init(...) in that
 * case), so this file behaves identically with or without Sentry wired.
 */
import * as Sentry from "@sentry/nextjs";
import NextError from "next/error";
import { useEffect } from "react";

export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body>
        <NextError statusCode={0} />
      </body>
    </html>
  );
}
