"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import { fetchRecommendation, type Recommendation } from "./development/api";

// Post-login hub. After the Google sign-in flow (StartCta) the user lands here
// instead of dropping straight into a lesson, and picks which practice mode to
// open: the existing real-time voice lessons (/learn) or the new vocabulary
// trainer (/satzschmiede). Both destinations run their own auth guard, so this
// page is purely a chooser.
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// REC-001: where each recommended pillar sends the learner.
const REC_TARGETS: Record<
  Recommendation["pillar"],
  { href: string; cta: string }
> = {
  satz: { href: "/satzschmiede", cta: "Practice words" },
  flow: { href: "/flow", cta: "Enter the Flow" },
  tandem: { href: "/tandem", cta: "Open Tandem" },
};

export default function PracticeMenu() {
  const { token, user, ready } = useAuth();
  const router = useRouter();

  // REC-001: today's data-driven pick, shown as a banner above the modes.
  // Non-fatal — any load failure just means no banner; the menu is the core.
  const [rec, setRec] = useState<Recommendation | null>(null);
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    fetchRecommendation(token)
      .then((r) => {
        if (!cancelled) setRec(r);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [token]);

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

        {/* REC-001: after ~3 active days this week, the data may pick one
            pillar to push today. Absent = no clear signal, and that's fine. */}
        {rec && (
          <div
            className="rise-in mt-8 flex flex-col gap-4 rounded-[28px] border-[3px] border-flag-gold-deep bg-flag-gold-soft p-6 sm:flex-row sm:items-center sm:justify-between"
            style={{ animationDelay: "80ms" }}
          >
            <div className="flex items-center gap-4">
              <Image
                src="/mascot/raven.png"
                alt=""
                width={44}
                height={44}
                className="h-11 w-11 shrink-0 select-none"
              />
              <div>
                <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink">
                  Today&apos;s recommendation
                </p>
                <p className="mt-1 max-w-xl font-body text-[15px] leading-snug text-ink-soft">
                  {rec.reason}
                </p>
              </div>
            </div>
            <Link
              href={REC_TARGETS[rec.pillar].href}
              className="btn-3d shrink-0 rounded-[16px] border-[3px] border-ink bg-white px-5 py-2.5 text-center font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink"
              style={inkShadow}
            >
              {REC_TARGETS[rec.pillar].cta} →
            </Link>
          </div>
        )}

        <div
          className="rise-in mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
          style={{ animationDelay: "120ms" }}
        >
          <ModeCard
            href="/satzschmiede"
            accent="gold"
            icon="pencil"
            kicker="Satzschmiede"
            title="Vocabulary Practice"
            body="Put new words to work in sentences of your own. A strict examiner checks each one and helps it stick."
            cta="Try it out"
          />
          <ModeCard
            href="/flow"
            accent="red"
            icon="infinity"
            kicker="alle Übungen · endlos"
            title="Flow"
            body="One stream, every exercise — words, endings, chunks, verb forms, articles, speaking — dealt one at a time until you say stop."
            cta="Go with the flow"
          />
          <ModeCard
            href="/tandem"
            accent="ink"
            icon="chat"
            kicker="Grammatik-Tandem"
            title="Tandem Partner"
            body="Daily German chat with Lena or Paul — everyday German or office German. Each remembers your talks and gently fixes the grammar you keep missing."
            cta="Meet your partners"
          />
        </div>

        {/* The rest of the modes — a beat of space below the top three. */}
        <div
          className="rise-in mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
          style={{ animationDelay: "160ms" }}
        >
          <ModeCard
            href="/teacher"
            accent="red"
            icon="bulb"
            kicker="Your teacher · in English"
            title="Ask the Teacher"
            body="Don't understand why? Clara explains the German you keep getting wrong — simply, in English, one thing at a time."
            cta="Ask Clara"
          />
          <ModeCard
            href="/genus"
            accent="gold"
            icon="circles"
            kicker="der · die · das"
            title="Artikel-Anker"
            body="Drag the color onto the ending — most endings give the gender away. Then prove it: eine neue Wohnung."
            cta="Drag the article"
          />
          <ModeCard
            href="/learn"
            accent="red"
            icon="mic"
            title="Conversation Practice"
            body="Real-time voice conversations with your AI partner — the scenario lessons you already know."
            cta="Start talking"
          />
          <ModeCard
            href="/bauteil"
            accent="red"
            icon="blocks"
            kicker="Bauteil-Sätze"
            title="Declension Practice"
            body="Raw parts in — ein · gut · Job — the right endings out. Read the sentence, spot the case, and put the flag where it belongs."
            cta="Start building"
          />
          <ModeCard
            href="/sprechen"
            accent="gold"
            icon="wave"
            kicker="Sprechen"
            title="Speaking Drills"
            body="Constrained speaking tasks that trap the structure — use weil twice, front every sentence — then see what you actually said."
            cta="Start speaking"
          />
          <ModeCard
            href="/verbformen"
            accent="ink"
            icon="clock"
            kicker="Verbformen"
            title="Past-Tense Verbs"
            body="Drill the spoken past of your own verbs — ist gefahren, hat gedacht, war, wollte — until the forms come without thinking."
            cta="Drill your verbs"
          />
          <ModeCard
            href="/verbindungen"
            accent="red"
            icon="link"
            kicker="Feste Verbindungen"
            title="Verb Chunks"
            body="sich freuen auf, warten auf, denken an — complete the chunk: pronoun, preposition, case. Mixed, so nothing is predictable."
            cta="Fill the gaps"
          />
          <ModeCard
            href="/szenario"
            accent="gold"
            icon="target"
            title="Szenario-Sparring"
            body="Answer unpredictable questions with clear structure."
            cta="Step into the scene"
          />
          <ModeCard
            href="/zeitfaerbung"
            accent="ink"
            icon="palette"
            kicker="war · wurde · blieb"
            title="Zeitfärbung"
            body="Same blank, different meaning — war, wurde, or blieb? Pick the Präteritum verb that fits what actually happened."
            cta="Pick the verb"
          />
        </div>

        <Link
          href="/development"
          className="rise-in btn-3d mt-6 flex items-center gap-5 rounded-[24px] border-[3px] border-ink bg-paper-warm px-6 py-5"
          style={{ ...inkShadow, animationDelay: "200ms" }}
        >
          <div className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl border-[3px] border-ink bg-ink text-white">
            <ChartIcon />
          </div>
          <div className="flex-1">
            <h2 className="font-display text-[17px] font-black tracking-tight text-ink">
              Your Development
            </h2>
            <p className="mt-0.5 font-body text-[13px] text-ink-soft">
              Your stats, your biggest errors, your wins
            </p>
          </div>
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-4 w-4 shrink-0 text-ink"
          >
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </Link>
      </main>
    </div>
  );
}

// Chart-ish glyph — three ascending bars — for the Development entry point.
// Kept local (not added to ModeIconName) since this card is deliberately not
// one of the exercise tiles.
function ChartIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-6 w-6"
    >
      <line x1="5" y1="19" x2="5" y2="13" />
      <line x1="12" y1="19" x2="12" y2="8" />
      <line x1="19" y1="19" x2="19" y2="4" />
    </svg>
  );
}

function ModeCard({
  href,
  accent,
  icon,
  title,
  kicker,
  body,
  cta,
}: {
  href: string;
  accent: "red" | "gold" | "ink";
  icon: ModeIconName;
  title: string;
  kicker?: string;
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

type ModeIconName =
  | "mic"
  | "pencil"
  | "chat"
  | "blocks"
  | "wave"
  | "clock"
  | "link"
  | "target"
  | "palette"
  | "infinity"
  | "circles"
  | "bulb";

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
  if (name === "target") {
    // a bullseye — "open with the point, land the anchor"
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="8.5" />
        <circle cx="12" cy="12" r="4.5" />
        <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
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
  if (name === "palette") {
    // paint palette with three dabs — "color the same blank three ways"
    return (
      <svg {...common}>
        <path d="M12 3a9 9 0 1 0 0 18c1.4 0 2.1-.8 2.1-1.9 0-.5-.2-1-.6-1.4-.4-.4-.6-.9-.6-1.4 0-1.1.9-2 2-2h2.1A3 3 0 0 0 20 11.6C19.8 6.9 16.3 3 12 3Z" />
        <circle cx="7.5" cy="10.5" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="12" cy="7.3" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="16.3" cy="10.5" r="1.1" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  if (name === "circles") {
    // three gender circles in a row — der · die · das
    return (
      <svg {...common}>
        <circle cx="5" cy="12" r="3" />
        <circle cx="12" cy="12" r="3" />
        <circle cx="19" cy="12" r="3" />
      </svg>
    );
  }
  if (name === "infinity") {
    // a lemniscate — "one endless stream, every exercise looping through"
    return (
      <svg {...common}>
        <path d="M9.828 9.172a4 4 0 1 0 0 5.656 10 10 0 0 0 2.172-2.828 10 10 0 0 1 2.172-2.828 4 4 0 1 1 0 5.656 10 10 0 0 1-2.172-2.828 10 10 0 0 0-2.172-2.828" />
      </svg>
    );
  }
  if (name === "bulb") {
    // lightbulb — "the moment it clicks"
    return (
      <svg {...common}>
        <path d="M9 18h6M10 21h4" />
        <path d="M12 3a6 6 0 0 1 3.6 10.8c-.7.5-1.1 1.3-1.1 2.2H9.5c0-.9-.4-1.7-1.1-2.2A6 6 0 0 1 12 3Z" />
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
