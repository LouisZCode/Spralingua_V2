import Link from "next/link";
import AppHeader from "@/components/shared/AppHeader";

// LEGAL-001: shared chrome for the three legal documents (Impressum, Privacy
// Policy, Terms of Service) — a quiet, readable column rather than a
// marketing surface. Matches the app's existing brand palette/fonts (see
// globals.css + LandingPage.tsx). Typography atoms for the document bodies
// themselves live in ./prose.tsx (imported directly by each page.tsx, not
// re-exported here — Next.js treats this file as a route special file).
const LEGAL_NAV: { href: string; label: string }[] = [
  { href: "/legal/impressum", label: "Impressum" },
  { href: "/legal/privacy", label: "Privacy Policy" },
  { href: "/legal/terms", label: "Terms of Service" },
];

export default function LegalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="relative min-h-screen bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      {/* APPHDR-001: shared header — moment pages (legal) scroll away with
          the content; the legal nav rides in the right slot, and the
          wordmark now leads back to the landing page. */}
      <AppHeader
        logoHref="/"
        sticky={false}
        maxWidth="max-w-3xl"
        right={
          <nav className="flex flex-wrap gap-x-5 gap-y-1">
            {LEGAL_NAV.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className="font-body text-[13px] font-semibold text-ink-soft transition-colors hover:text-flag-red"
              >
                {l.label}
              </Link>
            ))}
          </nav>
        }
      />

      <main className="relative mx-auto max-w-3xl px-6 py-14">
        <article>{children}</article>
      </main>
    </div>
  );
}
