import Link from "next/link";

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

      <header className="relative border-b-[3px] border-line bg-card/85 backdrop-blur">
        <div className="mx-auto flex max-w-3xl flex-col gap-3 px-6 py-6 sm:flex-row sm:items-center sm:justify-between">
          <Link
            href="/"
            className="font-body text-[13px] font-bold uppercase tracking-[0.18em] text-ink-muted transition-colors hover:text-flag-red"
          >
            ← Back to Spralingua
          </Link>
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
        </div>
      </header>

      <main className="relative mx-auto max-w-3xl px-6 py-14">
        <article>{children}</article>
      </main>
    </div>
  );
}
