"use client";

/**
 * UI-016 — branded 404. Without this file Next.js falls back to its stock,
 * unstyled 404 with no AppHeader, no way back to /practice, and (being
 * outside this app's theme system entirely) no dark-mode support. Client
 * component because it reads useAuth() to pick the back-target — same rule
 * as error.tsx and Pricing.tsx (PAYHUB-001): a token means /practice is
 * safe, otherwise the public landing page.
 */
import Link from "next/link";
import { useAuth } from "@/components/auth/AuthContext";
import AppHeader from "@/components/shared/AppHeader";

export default function NotFound() {
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
      />

      <main className="relative mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center px-6 py-16 text-center">
        <h1 className="font-display text-[26px] font-black leading-tight text-ink">
          Page not found
        </h1>
        <p className="mt-3 font-body text-[15px] leading-relaxed text-ink-soft">
          That link doesn&apos;t lead anywhere — the page may have moved or
          never existed.
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
