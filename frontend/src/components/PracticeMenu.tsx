"use client";

import { useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";

// Post-login hub. After the Google sign-in flow (StartCta) the user lands here
// instead of dropping straight into a lesson, and picks which practice mode to
// open: the existing real-time voice lessons (/learn) or the new vocabulary
// trainer (/satzschmiede). Both destinations run their own auth guard, so this
// page is purely a chooser.
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

export default function PracticeMenu() {
  const { token, user, ready } = useAuth();
  const router = useRouter();

  // Same guard the /learn route uses: once localStorage hydration settles, a
  // missing token bounces to the public landing page, where sign-in lives.
  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  // Don't flash the menu before auth is known, nor render it for a signed-out
  // visitor mid-redirect.
  if (!ready || !token) {
    return null;
  }

  const firstName = user?.name?.trim().split(/\s+/)[0] ?? "";

  return (
    <div className="relative flex min-h-screen flex-col bg-white text-ink">
      {/* Paper grain — same surface as the landing page */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      {/* Top bar — same wordmark as the landing header */}
      <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-2.5">
            <Image
              src="/mascot/raven.png"
              alt="Spralingua raven mascot"
              width={40}
              height={40}
              priority
              className="h-9 w-9 select-none"
            />
            <span className="font-display text-[22px] font-black tracking-tight text-ink">
              Spralingua
            </span>
          </Link>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-5xl flex-1 flex-col justify-center px-6 py-16">
        <div className="rise-in">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            {firstName ? `Welcome back, ${firstName}` : "Welcome back"}
          </p>
          <h1 className="mt-3 font-display text-[clamp(30px,5vw,50px)] font-black leading-[1.02] tracking-tight text-ink">
            How do you want to practice?
          </h1>
          <p className="mt-4 max-w-lg font-body text-[16px] leading-relaxed text-ink-soft">
            Pick a mode to get started. You can switch any time.
          </p>
        </div>

        <div
          className="rise-in mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
          style={{ animationDelay: "120ms" }}
        >
          <ModeCard
            href="/learn"
            accent="red"
            icon="mic"
            title="Conversation Practice"
            body="Real-time voice conversations with your AI partner — the scenario lessons you already know."
            cta="Start talking"
          />
          <ModeCard
            href="/satzschmiede"
            accent="gold"
            icon="pencil"
            kicker="Satzschmiede"
            badge="New"
            title="Vocabulary Practice"
            body="Put new words to work in sentences of your own. A strict examiner checks each one and helps it stick."
            cta="Try it out"
          />
          <ModeCard
            href="/tandem"
            accent="ink"
            icon="chat"
            kicker="Grammatik-Tandem"
            badge="New"
            title="Tandem Partner"
            body="Daily German chat with Lena, your language-exchange partner. She remembers your last talks and gently fixes the grammar you keep missing."
            cta="Meet Lena"
          />
          <ModeCard
            href="/bauteil"
            accent="red"
            icon="blocks"
            kicker="Bauteil-Sätze"
            badge="New"
            title="Declension Practice"
            body="Raw parts in — ein · gut · Job — the right endings out. Read the sentence, spot the case, and put the flag where it belongs."
            cta="Start building"
          />
          <ModeCard
            href="/sprechen"
            accent="gold"
            icon="wave"
            kicker="Sprechen"
            badge="New"
            title="Speaking Drills"
            body="Constrained speaking tasks that trap the structure — use weil twice, front every sentence — then see what you actually said."
            cta="Start speaking"
          />
          <ModeCard
            href="/verbformen"
            accent="ink"
            icon="clock"
            kicker="Verbformen"
            badge="New"
            title="Past-Tense Verbs"
            body="Drill the spoken past of your own verbs — ist gefahren, hat gedacht, war, wollte — until the forms come without thinking."
            cta="Drill your verbs"
          />
          <ModeCard
            href="/verbindungen"
            accent="red"
            icon="link"
            kicker="Feste Verbindungen"
            badge="New"
            title="Verb Chunks"
            body="sich freuen auf, warten auf, denken an — complete the chunk: pronoun, preposition, case. Mixed, so nothing is predictable."
            cta="Fill the gaps"
          />
        </div>
      </main>
    </div>
  );
}

function ModeCard({
  href,
  accent,
  icon,
  title,
  kicker,
  badge,
  body,
  cta,
}: {
  href: string;
  accent: "red" | "gold" | "ink";
  icon: ModeIconName;
  title: string;
  kicker?: string;
  badge?: string;
  body: string;
  cta: string;
}) {
  const chip =
    accent === "red"
      ? "bg-flag-red text-white"
      : accent === "gold"
        ? "bg-flag-gold text-ink"
        : "bg-ink text-white";
  return (
    <Link
      href={href}
      className="btn-3d flex flex-col rounded-[28px] border-[3px] border-ink bg-white p-7"
      style={inkShadow}
    >
      <div className="flex items-center justify-between">
        <div
          className={`grid h-14 w-14 place-items-center rounded-2xl border-[3px] border-ink ${chip}`}
        >
          <ModeIcon name={icon} />
        </div>
        {badge && (
          <span className="rounded-full border-[2px] border-ink bg-flag-gold-soft px-3 py-1 font-body text-[10px] font-black uppercase tracking-[0.18em] text-ink">
            {badge}
          </span>
        )}
      </div>
      {kicker && (
        <p className="mt-6 font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
          {kicker}
        </p>
      )}
      <h2
        className={`${kicker ? "mt-1" : "mt-6"} font-display text-[24px] font-black leading-tight text-ink`}
      >
        {title}
      </h2>
      <p className="mt-2 flex-1 font-body text-[15px] leading-relaxed text-ink-soft">
        {body}
      </p>
      <span className="mt-6 inline-flex items-center gap-2 font-display text-[14px] font-black uppercase tracking-[0.16em] text-ink">
        {cta}
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4 w-4"
        >
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
      </span>
    </Link>
  );
}

type ModeIconName = "mic" | "pencil" | "chat" | "blocks" | "wave" | "clock" | "link";

function ModeIcon({ name }: { name: ModeIconName }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className: "h-7 w-7",
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
  if (name === "blocks") {
    // three building blocks — "assemble the phrase from raw parts"
    return (
      <svg {...common}>
        <rect x="3.5" y="13" width="7" height="7" rx="1" />
        <rect x="13.5" y="13" width="7" height="7" rx="1" />
        <rect x="8.5" y="4" width="7" height="7" rx="1" />
      </svg>
    );
  }
  if (name === "wave") {
    // sound waves — "speak against the constraint"
    return (
      <svg {...common}>
        <line x1="4" y1="10" x2="4" y2="14" />
        <line x1="8" y1="7" x2="8" y2="17" />
        <line x1="12" y1="4" x2="12" y2="20" />
        <line x1="16" y1="7" x2="16" y2="17" />
        <line x1="20" y1="10" x2="20" y2="14" />
      </svg>
    );
  }
  if (name === "clock") {
    // clock turned back — "the spoken past"
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 7.5V12l3 2" />
      </svg>
    );
  }
  if (name === "link") {
    // chain links — "the verb and its fixed companions"
    return (
      <svg {...common}>
        <path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.2 1.2" />
        <path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.2-1.2" />
      </svg>
    );
  }
  if (name === "chat") {
    // two overlapping speech bubbles — "a conversation, back and forth"
    return (
      <svg {...common}>
        <path d="M7.5 8.5h9M7.5 12h5.5" />
        <path d="M3.5 6.5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9l-4 3.5V6.5Z" />
        <path d="M18 9.5h1.5a2 2 0 0 1 2 2v6L19 15" />
      </svg>
    );
  }
  // pencil — "write a sentence"
  return (
    <svg {...common}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}
