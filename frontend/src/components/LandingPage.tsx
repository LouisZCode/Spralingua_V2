import { Fragment } from "react";
import Image from "next/image";
import ThemeToggle from "./shared/ThemeToggle";
import Link from "next/link";
import HeroDemo from "./HeroDemo";
import StartCta from "./auth/StartCta";

type Accent = "red" | "gold" | "ink";

const FEATURES: {
  accent: Accent;
  icon: "mic" | "chat" | "check";
  title: string;
  body: string;
}[] = [
  {
    accent: "red",
    icon: "mic",
    title: "Talk, don't tap.",
    body: "Your microphone is the keyboard. Speak out loud, the way you actually would in the moment.",
  },
  {
    accent: "gold",
    icon: "chat",
    title: "Real scenarios.",
    body: "Run into a neighbor, introduce yourself on the street. Your partner stays in character the whole way through.",
  },
  {
    accent: "ink",
    icon: "check",
    title: "Get feedback.",
    body: "After each session: whether you hit the scenario's goals, plus your best and toughest phrase by pronunciation.",
  },
];

const STEPS: { n: string; title: string; body: string }[] = [
  {
    n: "01",
    title: "Pick a scenario",
    body: "Choose your level and the situation you want to practice.",
  },
  {
    n: "02",
    title: "Have the conversation",
    body: "Mic on. Talk in real time — it listens, thinks, and answers back.",
  },
  {
    n: "03",
    title: "See your results",
    body: "Goals checked off, and a pronunciation read on the phrases you said.",
  },
];

const LEVELS: { tag: string; level: string; title: string; sub: string }[] = [
  {
    tag: "A1",
    level: "Beginner",
    title: "Sidewalk Hello",
    sub: "First encounters · introducing yourself",
  },
  {
    tag: "B1",
    level: "Intermediate",
    title: "Waiting Room Run-In",
    sub: "Small talk while you both wait",
  },
];

const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;
const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;

export default function LandingPage() {
  return (
    <div className="relative min-h-screen bg-paper text-ink">
      {/* Paper grain — spans the full document, scrolls with content */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      {/* Top bar */}
      <header className="sticky top-0 z-50 border-b-[3px] border-line bg-card/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2.5">
            <Image
              src="/mascot/raven.png"
              alt="Spralingua raven mascot"
              width={40}
              height={40}
              priority
              className="mascot-keyline h-9 w-9 select-none"
            />
            <span className="font-display text-[22px] font-black tracking-tight text-ink">
              Spralingua
            </span>
          </div>
          <nav className="flex items-center gap-6">
            <Link
              href="/pricing"
              className="font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
            >
              Pricing
            </Link>
            <StartCta className="font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red">
              Start →
            </StartCta>
            {/* DARK-001: reachable before sign-in — the landing page is the
                first impression, and a visitor on a dark OS lands here first. */}
            <ThemeToggle />
          </nav>
        </div>
      </header>

      <main className="relative overflow-x-clip">
        <div className="mx-auto max-w-6xl px-6">
          {/* ─── Hero ─────────────────────────────────────────────── */}
          <section className="relative grid items-center gap-12 py-16 lg:grid-cols-2 lg:py-24">
            {/* Bauhaus decor */}
            <div
              aria-hidden
              className="geo-decor pointer-events-none absolute -left-8 top-2 h-20 w-20 rotate-12 border-[3px] border-line opacity-70"
            />

            <div className="rise-in">
              <h1 className="font-display text-[clamp(38px,6.5vw,68px)] font-black leading-[0.98] tracking-tight text-ink">
                Learn a language by{" "}
                <span className="goal-shimmer">speaking</span>
                {/* Raven end-cap: inline so it rides at the end of the line and
                    reflows with the headline — no absolute coords to break on
                    resize. Sized in em so it scales with the fluid headline.
                    The image is scaled past the circle and pushed up so head +
                    waving wing fill the badge while the feet crop away. */}
                <span className="relative ml-[0.2em] inline-block h-[1.45em] w-[1.45em] -rotate-3 overflow-hidden rounded-full border-[3px] border-line bg-flag-gold-soft align-middle shadow-[0_4px_0_var(--color-line)]">
                  <Image
                    src="/mascot/raven.png"
                    alt="Spralingua raven waving hello"
                    width={220}
                    height={220}
                    priority
                    className="mascot-keyline pointer-events-none absolute left-1/2 top-[-4%] w-[150%] max-w-none -translate-x-1/2 select-none"
                  />
                </span>
              </h1>
              <p className="mt-5 max-w-[440px] font-body text-[17px] leading-relaxed text-ink-soft">
                Real-time voice conversations with an AI partner.
                <br />
                Practice real situations, get immediate feedback.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row">
                <StartCta
                  className="btn-3d inline-flex items-center justify-center gap-2.5 rounded-[24px] border-[3px] border-red-line bg-flag-red-fill px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.16em] text-on-fill"
                  style={redShadow}
                >
                  <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current">
                    <path d="M6 4 L20 12 L6 20 Z" />
                  </svg>
                  Start a lesson
                </StartCta>
                <a
                  href="#how"
                  className="btn-3d inline-flex items-center justify-center rounded-[24px] border-[3px] border-line bg-card px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.16em] text-ink"
                  style={inkShadow}
                >
                  See how it works
                </a>
              </div>
            </div>

            {/* Hero visual — interactive: tap the orb to talk to the AI */}
            <div className="rise-in" style={{ animationDelay: "120ms" }}>
              <HeroDemo />
            </div>
          </section>

          {/* ─── What it is ───────────────────────────────────────── */}
          <section className="py-16 lg:py-24">
            <SectionHeading
              eyebrow="What it is"
              title="Not flashcards — a conversation."
            />
            <div className="mt-12 grid gap-5 sm:grid-cols-3">
              {FEATURES.map((f) => (
                <FeatureCard key={f.title} {...f} />
              ))}
            </div>
          </section>

          {/* ─── How it works ─────────────────────────────────────── */}
          <section id="how" className="scroll-mt-24 py-16 lg:py-24">
            <SectionHeading
              eyebrow="How it works"
              title="Talking in three steps."
            />
            <div className="mt-12 grid gap-10 sm:grid-cols-3">
              {STEPS.map((s) => (
                <div key={s.n} className="relative">
                  <span className="font-display text-[44px] font-black leading-none text-flag-red">
                    {s.n}
                  </span>
                  <h3 className="mt-3 font-display text-[21px] font-black leading-tight text-ink">
                    {s.title}
                  </h3>
                  <p className="mt-2 font-body text-[15px] leading-relaxed text-ink-soft">
                    {s.body}
                  </p>
                </div>
              ))}
            </div>
          </section>

          {/* ─── Levels ───────────────────────────────────────────── */}
          <section className="py-16 lg:py-24">
            <SectionHeading eyebrow="Levels" title="Start where you are." />
            <div className="mx-auto mt-12 flex max-w-md flex-col">
              {LEVELS.map((lv, i) => (
                <Fragment key={lv.tag}>
                  {i > 0 && (
                    <div
                      aria-hidden
                      className="dot-connector ml-[38px] h-12 w-1"
                    />
                  )}
                  <div className="flex items-center gap-5">
                    <div className="grid h-20 w-20 shrink-0 place-items-center rounded-full border-[4px] border-line bg-card shadow-[0_5px_0_var(--color-line)]">
                      <span className="font-display text-[22px] font-black text-ink">
                        {lv.tag}
                      </span>
                    </div>
                    <div>
                      <p className="font-body text-[10px] font-bold uppercase tracking-[0.22em] text-ink-muted">
                        {lv.level}
                      </p>
                      <h3 className="font-display text-[20px] font-black leading-tight text-ink">
                        {lv.title}
                      </h3>
                      <p className="font-body text-[14px] leading-snug text-ink-soft">
                        {lv.sub}
                      </p>
                    </div>
                  </div>
                </Fragment>
              ))}
              <div aria-hidden className="dot-connector ml-[38px] h-12 w-1" />
              <div className="flex items-center gap-5 opacity-60">
                <div className="grid h-20 w-20 shrink-0 place-items-center rounded-full border-[4px] border-dashed border-ink-faint bg-paper">
                  <span className="font-display text-[26px] font-black leading-none text-ink-faint">
                    +
                  </span>
                </div>
                <div>
                  <h3 className="font-display text-[20px] font-black leading-tight text-ink-muted">
                    More on the way
                  </h3>
                  <p className="font-body text-[14px] leading-snug text-ink-muted">
                    New levels and situations are in the works.
                  </p>
                </div>
              </div>
            </div>
          </section>

          {/* ─── Closing CTA ──────────────────────────────────────── */}
          <section className="py-16 lg:py-24">
            <div className="relative overflow-hidden rounded-[40px] border-[3px] border-line bg-ink-fill px-8 py-16 text-center shadow-[0_8px_0_var(--color-red-line)]">
              <div
                aria-hidden
                className="pointer-events-none absolute -left-12 -top-12 h-48 w-48 rounded-full bg-flag-gold/25"
              />
              <div
                aria-hidden
                className="pointer-events-none absolute -bottom-12 -right-8 h-44 w-44 bg-flag-red-fill/70"
                style={{ clipPath: "polygon(100% 0, 100% 100%, 0 100%)" }}
              />
              <div className="relative">
                <h2 className="font-display text-[clamp(28px,5vw,48px)] font-black leading-[1.02] text-on-fill">
                  Ready to talk?
                </h2>
                <p className="mx-auto mt-4 max-w-md font-body text-[16px] leading-relaxed text-on-fill/70">
                  Pick a scenario and start speaking. Mic on, 15-minute cap,
                  ends naturally when you say goodbye.
                </p>
                <StartCta
                  className="btn-3d mt-8 inline-flex items-center justify-center gap-2.5 rounded-[24px] border-[3px] border-red-line bg-flag-red-fill px-8 py-4 font-display text-[16px] font-black uppercase tracking-[0.16em] text-on-fill"
                  style={redShadow}
                >
                  <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current">
                    <path d="M6 4 L20 12 L6 20 Z" />
                  </svg>
                  Start a lesson
                </StartCta>
              </div>
            </div>
          </section>
        </div>
      </main>

      {/* Footer */}
      <footer className="relative border-t-[3px] border-line bg-card">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-6 py-8 sm:flex-row">
          <div className="flex items-center gap-2">
            <Image
              src="/mascot/raven.png"
              alt="Spralingua raven mascot"
              width={32}
              height={32}
              className="mascot-keyline h-7 w-7 select-none"
            />
            <span className="font-display text-[16px] font-black tracking-tight text-ink">
              Spralingua
            </span>
          </div>
          <span className="font-body text-[11px] uppercase tracking-[0.22em] text-ink-muted">
            Voice-powered language learning
          </span>
          {/* LEGAL-001: quiet links to the three legal documents. */}
          <nav className="flex items-center gap-4">
            <Link
              href="/pricing"
              className="font-body text-[11px] uppercase tracking-[0.22em] text-ink-muted transition-colors hover:text-flag-red"
            >
              Pricing
            </Link>
            <Link
              href="/legal/impressum"
              className="font-body text-[11px] uppercase tracking-[0.22em] text-ink-muted transition-colors hover:text-flag-red"
            >
              Impressum
            </Link>
            <Link
              href="/legal/privacy"
              className="font-body text-[11px] uppercase tracking-[0.22em] text-ink-muted transition-colors hover:text-flag-red"
            >
              Privacy
            </Link>
            <Link
              href="/legal/terms"
              className="font-body text-[11px] uppercase tracking-[0.22em] text-ink-muted transition-colors hover:text-flag-red"
            >
              Terms
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}

function SectionHeading({
  eyebrow,
  title,
}: {
  eyebrow: string;
  title: React.ReactNode;
}) {
  return (
    <div className="max-w-2xl">
      <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
        {eyebrow}
      </p>
      <h2 className="mt-3 font-display text-[clamp(28px,4.5vw,44px)] font-black leading-[1.05] tracking-tight text-ink">
        {title}
      </h2>
    </div>
  );
}

function FeatureCard({
  accent,
  icon,
  title,
  body,
}: {
  accent: Accent;
  icon: "mic" | "chat" | "check";
  title: string;
  body: string;
}) {
  const chip =
    accent === "red"
      ? "bg-flag-red-fill text-on-fill"
      : accent === "gold"
        ? "bg-flag-gold text-ink-fixed"
        : "bg-ink-fill text-on-fill";
  return (
    <div className="lift-card rounded-[28px] border-[3px] border-line bg-card p-6">
      <div
        className={`grid h-12 w-12 place-items-center rounded-2xl border-[3px] border-line ${chip}`}
      >
        <FeatureIcon name={icon} />
      </div>
      <h3 className="mt-5 font-display text-[22px] font-black leading-tight text-ink">
        {title}
      </h3>
      <p className="mt-2 font-body text-[15px] leading-relaxed text-ink-soft">
        {body}
      </p>
    </div>
  );
}

function FeatureIcon({ name }: { name: "mic" | "chat" | "check" }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className: "h-6 w-6",
  };
  if (name === "mic") {
    return (
      <svg {...common}>
        <rect x="9" y="3" width="6" height="11" rx="3" />
        <path d="M5 11a7 7 0 0 0 14 0" />
        <line x1="12" y1="18" x2="12" y2="22" />
      </svg>
    );
  }
  if (name === "chat") {
    return (
      <svg {...common}>
        <path d="M4 5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M20 7 L10 17 L5 12" />
    </svg>
  );
}
