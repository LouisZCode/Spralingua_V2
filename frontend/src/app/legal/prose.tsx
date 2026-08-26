// LEGAL-001: shared typography atoms for the three legal documents
// (Impressum, Privacy Policy, Terms of Service). Not a route file — Next.js
// only treats page.tsx/layout.tsx/etc. as special, so this plain module can
// export as many named helpers as it likes. Server components throughout:
// no "use client", no runtime markdown parsing — each legal/*/page.tsx just
// assembles these around the verbatim document text.

export function LegalTitle({ children }: { children: React.ReactNode }) {
  return (
    <h1 className="font-display text-[clamp(30px,5vw,42px)] font-black leading-tight tracking-tight text-ink">
      {children}
    </h1>
  );
}

export function LegalMeta({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-3 font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink-muted">
      {children}
    </p>
  );
}

export function LegalH2({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-12 border-t-[3px] border-rule pt-8 font-display text-[22px] font-black leading-tight tracking-tight text-ink first:mt-8 first:border-t-0 first:pt-0">
      {children}
    </h2>
  );
}

export function LegalH3({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mt-6 font-display text-[17px] font-black tracking-tight text-ink">
      {children}
    </h3>
  );
}

export function LegalP({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-4 font-body text-[16px] leading-[1.75] text-ink-soft">
      {children}
    </p>
  );
}

// A short block of address/contact lines — rendered on separate lines
// without paragraph spacing between them, the way a mailing address reads.
export function LegalLines({ lines }: { lines: string[] }) {
  return (
    <p className="mt-4 font-body text-[16px] leading-[1.6] text-ink-soft">
      {lines.map((line, i) => (
        <span key={i}>
          {line}
          {i < lines.length - 1 && <br />}
        </span>
      ))}
    </p>
  );
}

export function LegalUL({ children }: { children: React.ReactNode }) {
  return (
    <ul className="mt-4 list-disc space-y-2 pl-6 font-body text-[16px] leading-[1.7] text-ink-soft marker:text-ink-faint">
      {children}
    </ul>
  );
}

// Bold inline emphasis — matches the source markdown's **text**.
export function B({ children }: { children: React.ReactNode }) {
  return <strong className="font-bold text-ink">{children}</strong>;
}

// Markdown table → a real <table>, wrapped so it scrolls horizontally on its
// own rather than pushing the page body sideways on mobile.
export function LegalTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: React.ReactNode[][];
}) {
  return (
    <div className="mt-4 overflow-x-auto rounded-[16px] border-[3px] border-line">
      <table className="w-full min-w-[560px] border-collapse font-body text-[14px] leading-relaxed">
        <thead>
          <tr className="bg-paper-warm">
            {headers.map((h, i) => (
              <th
                key={i}
                className="border-b-[3px] border-line px-4 py-3 text-left font-display text-[12px] font-black uppercase tracking-[0.06em] text-ink"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className="border-b border-rule align-top last:border-b-0"
            >
              {row.map((cell, j) => (
                <td key={j} className="px-4 py-3 text-ink-soft">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
