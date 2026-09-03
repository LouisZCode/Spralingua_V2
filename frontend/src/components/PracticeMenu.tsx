"use client";

import { useEffect, useRef, useState } from "react";
import ThemeToggle from "./shared/ThemeToggle";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import LevelPickerModal from "./LevelPickerModal";
import { CoinPill, useCoinBalance } from "./shared/Coins";
import {
  fetchRecommendation,
  fetchStats,
  type Recommendation,
  type Streak,
} from "./development/api";
import AppHeader from "@/components/shared/AppHeader";

// Post-login hub. After the Google sign-in flow (StartCta) the user lands here
// instead of dropping straight into a lesson, and picks which practice mode to
// open: the existing real-time voice lessons (/learn) or the new vocabulary
// trainer (/satzschmiede). Both destinations run their own auth guard, so this
// page is purely a chooser.
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
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

// LEVEL-001: records that the level question has been PUT to this learner on
// this device, so declining it ("Not sure", which stores null) doesn't make
// the modal reappear on every visit.
const LEVEL_ASKED_KEY = "spralingua_level_asked";

// GAME-001: streak days are UTC in this app, so the meter's "seen" cache key
// is scoped to the UTC calendar day — a stored value from a prior day simply
// lives under a different key and is never read.
function utcDateString(): string {
  const now = new Date();
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, "0");
  const d = String(now.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export default function PracticeMenu() {
  const { token, user, ready, setLevel } = useAuth();
  // LEVEL-001: the one-time level question (see the effect below).
  const [askLevel, setAskLevel] = useState(false);
  const router = useRouter();
  const isDev = user?.role === "developer";

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

  // GAME-001: the streak banner above the recommendation. Non-fatal — any load
  // failure just means no banner; only the streak object is kept out of stats.
  const [streak, setStreak] = useState<Streak | null>(null);
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    fetchStats(token)
      .then((s) => {
        if (!cancelled) setStreak(s.streak);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [token]);

  // GAME-001: the segmented meter animates from what the learner last saw to
  // what's true now, rather than snapping cold into a state they didn't earn
  // in front of them. meterBaseline is the count read back from localStorage
  // (scoped to today's UTC day — a stale key from a prior day is never read,
  // so it naturally defaults to 0); meterDisplay is what's actually rendered,
  // starting at the baseline and jumping to the live count after a beat, with
  // each segment's fill transition staggered so they read as filling one by
  // one. meterCelebrate is a one-shot pulse fired only when that fill crosses
  // into a full meter. meterRanRef makes sure this plays once per mount, not
  // once per streak re-fetch.
  const [meterDisplay, setMeterDisplay] = useState(0);
  const [meterBaseline, setMeterBaseline] = useState(0);
  const [meterCelebrate, setMeterCelebrate] = useState(false);
  const meterRanRef = useRef(false);
  useEffect(() => {
    if (!streak || meterRanRef.current) return;
    meterRanRef.current = true;

    const key = `streakMeterSeen:${utcDateString()}`;
    let seen = 0;
    try {
      const raw = window.localStorage.getItem(key);
      const parsed = raw === null ? NaN : Number(raw);
      if (Number.isFinite(parsed)) seen = parsed;
    } catch {
      // localStorage unavailable (private mode, etc.) — start from 0.
    }

    const target = streak.modesToday.length;

    let beatId: ReturnType<typeof setTimeout>;
    let celebrateId: ReturnType<typeof setTimeout>;
    // All setState goes through the rAF callback rather than the effect body
    // (react-hooks/set-state-in-effect) — one extra frame at zero segments
    // before the baseline paints, invisible in practice.
    const rafId = requestAnimationFrame(() => {
      setMeterBaseline(seen);
      setMeterDisplay(seen);
      if (seen === target) return;
      beatId = setTimeout(() => {
        setMeterDisplay(target);
        const becameFull =
          seen < streak.modesRequired && target >= streak.modesRequired;
        if (becameFull) {
          const fillMs = 300 + Math.max(0, target - seen) * 90;
          celebrateId = setTimeout(() => setMeterCelebrate(true), fillMs);
        }
        try {
          window.localStorage.setItem(key, String(target));
        } catch {
          // Non-fatal — worst case the animation just replays next visit.
        }
      }, 300);
    });

    return () => {
      cancelAnimationFrame(rafId);
      clearTimeout(beatId);
      clearTimeout(celebrateId);
    };
  }, [streak]);

  // Same guard the /learn route uses: once localStorage hydration settles, a
  // missing token bounces to the public landing page, where sign-in lives.
  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  // LEVEL-001: ask once, here, because /practice is the hub every signed-in
  // learner passes through. `user.level === null` alone can't gate this —
  // "Not sure" also stores null, and re-asking someone who declined on every
  // visit would be nagging. The local flag records that the question was PUT
  // to them; a new device asks once more, which is cheap and harmless.
  useEffect(() => {
    if (!ready || !token || !user) return;
    if (user.level) return;
    try {
      if (window.localStorage.getItem(LEVEL_ASKED_KEY) === "1") return;
    } catch {
      // localStorage unavailable (private mode) — ask, it's one modal.
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAskLevel(true);
  }, [ready, token, user]);

  // Don't flash the menu before auth is known, nor render it for a signed-out
  // visitor mid-redirect.
  if (!ready || !token) {
    return null;
  }

  const firstName = user?.name?.trim().split(/\s+/)[0] ?? "";

  return (
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      {/* LEVEL-001: the one-time level question. Marking it asked BEFORE the
          PUT resolves is deliberate — a failed save shouldn't strand the
          learner behind a modal they can't dismiss; they can set it from the
          header chip below whenever they like. */}
      {askLevel && (
        <LevelPickerModal
          onDone={async (level) => {
            try {
              window.localStorage.setItem(LEVEL_ASKED_KEY, "1");
            } catch {
              // Private mode — they'll be asked again next visit.
            }
            await setLevel(level);
            setAskLevel(false);
          }}
        />
      )}

      {/* Paper grain — same surface as the landing page */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      {/* Top bar — same wordmark as the landing header */}
      {/* APPHDR-001: shared header — the hub carries the learner's real
          state (theme, coins, pricing door, level) and, when signed in, the
          profile avatar. The landing page passes its own marketing nav. */}
      <AppHeader
        right={
          <>
            {/* DARK-001: the light/dark switch lives here because /practice is
                the one header every learner passes through. */}
            <ThemeToggle />
            {/* PAY-002: balance chip — informative even at 0, hidden when signed out */}
            <PracticeMenuCoinChip />
            {/* PAY-001/PAYHUB-001: the door to /pricing adapts to the plan.
                Signed out or free → visible (compare plans, upgrade, top-up).
                Paying → not in the header at all; /profile's "Manage plan →"
                is their plan door (portal, tier switch), so the hub's
                money-nav is reserved for the people it converts. The
                out-of-coins panels stay tier-blind — running dry is a
                money moment for every plan. */}
            {/* APPHDR-002: hidden on a phone so it can't collide with the
                wordmark/avatar; UX-16: py-3.5 -my-3.5 grows the tap target to
                ~44px without shifting anything visually. */}
            {(user?.tier ?? "free") !== "basic" && (user?.tier ?? "free") !== "premium" && (
              <Link
                href="/pricing"
                className="hidden -my-3.5 py-3.5 font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink-muted transition-colors hover:text-flag-red sm:inline"
              >
                Pricing
              </Link>
            )}

            {/* LEVEL-001: the level is always visible and always changeable —
                a self-declared level goes stale as the learner improves, and
                one that can't be corrected is worse than none. */}
            <button
              type="button"
              onClick={() => setAskLevel(true)}
              className="rounded-full border-[3px] border-line bg-paper-warm px-4 py-1.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-ink"
              title="Change your level"
            >
              {user?.level ?? "Set level"}
            </button>
          </>
        }
      />

      <main className="relative mx-auto flex w-full max-w-5xl flex-1 flex-col justify-center px-6 py-16">
        <div className="rise-in">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            {firstName ? `Welcome back, ${firstName}` : "Welcome back"}
          </p>
          <h1 className="mt-3 font-display text-[clamp(30px,5vw,50px)] font-black leading-[1.02] tracking-tight text-ink">
            How do you want to practice?
          </h1>
        </div>

        {/* GAME-001: forgiving daily streak, free weekly grace day, longest is
            a permanent PR. A day is now earned by completing 3 of the 4
            practice modes below, not by a single graded attempt — the
            progress bar just under this badge is the 0-3 counter for that. It
            sits here — above the recommendation, in the first thing the eye
            lands on after the headline — rather than as the old header badge,
            which was small enough to miss entirely and hid itself at zero.
            Always rendered once loaded: a visible 0 is what invites the first
            day. */}
        {streak && (
          <div
            className="rise-in mt-6 flex flex-wrap items-center justify-center gap-x-4 gap-y-2 text-center"
            style={{ animationDelay: "40ms" }}
          >
            <div
              className={`flex items-center gap-2 rounded-2xl border-[3px] border-line px-4 py-2 ${
                streak.practicedToday
                  ? "bg-flag-gold text-ink-fixed"
                  : "bg-card text-ink"
              }`}
            >
              <FlameIcon muted={!streak.practicedToday} />
              <span className="font-display text-[24px] font-black leading-none">
                {streak.current}
              </span>
              <span className="font-body text-[12px] font-bold uppercase tracking-[0.18em]">
                {streak.current === 1 ? "day" : "days"}
              </span>
            </div>
            <p className="font-body text-[15px] leading-snug text-ink-soft">
              {streakNote(streak)}
            </p>
          </div>
        )}

        {/* GAME-001: silent progress toward today's 3-of-4 mode requirement,
            segmented so each mode's contribution reads as a discrete step
            rather than a fraction of a bar. No caption, no numbers, in any
            state — it sits under the streak badge, not beside it, so it
            never competes for the eye. Same non-fatal rule as the badge
            above: renders only once streak has loaded. The fill-on-return
            animation (meterBaseline → meterDisplay) lives in the effect
            above; this block just renders whatever it's told. */}
        {streak && (
          <div
            className="rise-in mx-auto mt-3 w-full max-w-[220px]"
            style={{ animationDelay: "50ms" }}
          >
            <div
              className={`flex gap-2 ${meterCelebrate ? "meter-celebrate" : ""}`}
            >
              {Array.from({ length: streak.modesRequired }, (_, i) => (
                <div
                  key={i}
                  className={`h-3 flex-1 rounded-full border-[3px] border-line transition-colors duration-300 ease-out ${
                    i < meterDisplay
                      ? meterDisplay >= streak.modesRequired
                        ? "bg-success"
                        : "bg-flag-gold"
                      : "bg-card"
                  }`}
                  style={{
                    transitionDelay: `${Math.max(0, i - meterBaseline) * 90}ms`,
                  }}
                />
              ))}
            </div>
          </div>
        )}

        {/* AGENT-001: Clara stands alone above the three practice pillars —
            she isn't a drill, she's who you ask when a drill made no sense, so
            burying her among the modes hid the one thing that explains the
            others. Full width on purpose: this is the promotion, not a card. */}
        <div className="rise-in mt-10" style={{ animationDelay: "80ms" }}>
          <ModeCard
            href="/teacher"
            accent="red"
            icon="bulb"
            kicker="Your teacher · in English"
            title="Clara the Teacher" // CLARA-19: plain card copy
            body="Ask her about any topic."
            cta="Ask Clara"
            // PAY-004: the wall should be visible on the menu, not after a
            // friend has already picked a topic and typed a question.
            badge={
              isDev || (user?.tier ?? "free") !== "free" ? undefined : "Basic"
            }
          />
        </div>

        {/* MVP-001: the four core exercises, and only these four. Every other
            drill still exists and still runs — it is reached from the dev
            block at the bottom of /development, and, for the single-grammar
            ones, from inside the Flow, which is where a learner meets them.
            Two columns rather than three: four cards divide evenly, and the
            extra width is what makes these read as the whole product rather
            than the first row of a longer list. */}
        <div
          className="rise-in mt-10 grid gap-6 sm:grid-cols-2"
          style={{ animationDelay: "100ms" }}
        >
          <ModeCard
            href="/satzschmiede"
            accent="gold"
            icon="pencil"
            kicker="Satzschmiede"
            title="Vocabulary Practice"
            body="Put new words to work in sentences of your own. A strict examiner checks each one and helps it stick."
            cta="Try it out"
            done={streak?.modesToday.includes("satz")}
          />
          <ModeCard
            href="/flow"
            accent="red"
            icon="infinity"
            kicker="alle Übungen · endlos"
            title="Flow"
            body="One stream, every exercise — words, endings, chunks, verb forms, articles, cases, clauses, speaking — dealt one at a time until you say stop."
            cta="Go with the flow"
            done={streak?.modesToday.includes("flow")}
          />
          <ModeCard
            href="/tandem"
            accent="ink"
            icon="chat"
            kicker="Grammatik-Tandem"
            title="Tandem Partner"
            body="Daily German chat with Lena or Paul — everyday German or office German. Each remembers your talks and gently fixes the grammar you keep missing."
            cta="Meet your partners"
            done={streak?.modesToday.includes("tandem")}
          />
          <ModeCard
            href="/briefkasten"
            accent="gold"
            icon="mail"
            kicker="Briefkasten"
            title="Letter Writing"
            body="A letter arrives — reply in German. Hints first, then corrections, then how a German would really say it."
            cta="Write back"
            done={streak?.modesToday.includes("briefkasten")}
          />
        </div>

        {/* Developer-only: Interview exercise (not yet fully surfaced) */}
        {isDev && (
          <div className="rise-in mt-10" style={{ animationDelay: "120ms" }}>
            <ModeCard
              href="/interview"
              accent="red"
              icon="mic"
              kicker="Developer Preview"
              title="Interview Practice"
              body="Audio comprehension exercise — listen and answer. 20 coins per answer chunk. Work in progress."
              cta="Try Interview"
              done={false}
            />
          </div>
        )}

        {/* REC-001: after ~3 active days this week, the data may pick one
            pillar to push today. Absent = no clear signal, and that's fine.
            Sits below the four pillars now rather than above them — a pick
            among the modes reads better once the learner has already seen
            what the modes are. */}
        {rec && (
          <div
            className="rise-in mt-6 flex flex-col gap-4 rounded-[28px] border-[3px] border-flag-gold-deep bg-flag-gold-soft p-6 sm:flex-row sm:items-center sm:justify-between"
            style={{ animationDelay: "140ms" }}
          >
            <div className="flex items-center gap-4">
              <Image
                src="/mascot/raven.png"
                alt=""
                width={44}
                height={44}
                className="mascot-keyline h-11 w-11 shrink-0 select-none"
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
              className="btn-3d shrink-0 rounded-[16px] border-[3px] border-line bg-card px-5 py-2.5 text-center font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink"
              style={inkShadow}
            >
              {REC_TARGETS[rec.pillar].cta} →
            </Link>
          </div>
        )}

        <Link
          href="/development"
          className="rise-in btn-3d mt-6 flex items-center gap-5 rounded-[24px] border-[3px] border-line bg-paper-warm px-6 py-5"
          style={{ ...inkShadow, animationDelay: "200ms" }}
        >
          <div className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl border-[3px] border-line bg-ink-fill text-on-fill">
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

        {/* LEGAL-001: discreet in-app reachability for the legal docs — the
            two-click rule (home footer is the other click). Small and muted
            on purpose; this isn't a feature, just a reachable link.
            UX-16: py-3.5 -my-3.5 grows each tap target to ~44px tall without
            shifting anything visually (the negative margin cancels the
            padding's effect on layout). */}
        <footer className="mt-10 flex justify-center gap-4">
          <Link
            href="/legal/impressum"
            className="-my-3.5 py-3.5 font-body text-[11px] uppercase tracking-[0.18em] text-ink-faint transition-colors hover:text-ink-muted"
          >
            Impressum
          </Link>
          <Link
            href="/legal/privacy"
            className="-my-3.5 py-3.5 font-body text-[11px] uppercase tracking-[0.18em] text-ink-faint transition-colors hover:text-ink-muted"
          >
            Privacy
          </Link>
          <Link
            href="/legal/terms"
            className="-my-3.5 py-3.5 font-body text-[11px] uppercase tracking-[0.18em] text-ink-faint transition-colors hover:text-ink-muted"
          >
            Terms
          </Link>
        </footer>
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

// The line beside the streak badge exists for one message only: you are at your
// personal best. Any other state — a broken streak, a good-but-not-record run —
// says nothing at all, because a running count of days you didn't beat is a
// scoreboard against yourself, and the forgiving design has no use for one.
function streakNote(streak: Streak): string | null {
  if (streak.current > 0 && streak.current >= streak.longest) {
    return "This is your longest streak yet.";
  }
  return null;
}

// Streak flame (GAME-001) — filled red when today already counts, an outline
// when it doesn't yet. Kept local like ChartIcon, one-off for the streak badge.
function FlameIcon({ muted }: { muted: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill={muted ? "none" : "currentColor"}
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`h-5 w-5 ${muted ? "text-ink-muted" : "text-flag-red"}`}
    >
      <path d="M12 3c1 3-3 4.5-3 8a3.5 3.5 0 0 0 7 0c0-1.5-.7-2.6-1.5-3.5-.4 1-.9 1.5-1.5 2C13.5 7 14 5 12 3Z" />
    </svg>
  );
}

// DARK-003: the one place accent -> visual treatment is decided for a
// ModeCard. Chip stays exactly what it was; `wash` is a no-op class in
// light mode (see globals.css) and a soft directional brand tint in dark.
const MODE_ACCENT_STYLES: Record<
  "red" | "gold" | "ink",
  { chip: string; wash: string }
> = {
  red: { chip: "bg-flag-red-fill text-on-fill", wash: "wash-red" },
  gold: { chip: "bg-flag-gold text-ink-fixed", wash: "wash-gold" },
  ink: { chip: "bg-ink-fill text-on-fill", wash: "wash-ink" },
};

function ModeCard({
  href,
  accent,
  icon,
  title,
  kicker,
  body,
  cta,
  done,
  badge,
}: {
  href: string;
  accent: "red" | "gold" | "ink";
  icon: ModeIconName;
  title: string;
  kicker?: string;
  body: string;
  cta: string;
  // GAME-001: true once this mode is already in today's modesToday. Never
  // disables the card — done just means "already counted today", not
  // "nothing left to do here".
  done?: boolean;
  // PAY-004: small tier chip top-right (e.g. Clara's "Basic"), so the pay
  // wall is visible before a learner invests time picking a topic. Rendered
  // in the same accent as the icon chip so the card keeps one palette.
  badge?: string;
}) {
  // DARK-003: chip tint and card wash come off the same accent — one
  // lookup instead of two parallel ternary chains that could drift apart.
  const { chip, wash } = MODE_ACCENT_STYLES[accent];
  return (
    <Link
      href={href}
      className={`btn-3d flex flex-col rounded-[28px] border-[3px] border-line p-7 ${
        done ? "bg-flag-gold-soft" : `bg-card ${wash}`
      }`}
      style={inkShadow}
    >
      <div className="flex items-center justify-between">
        <div className="relative">
          <div
            className={`grid h-14 w-14 place-items-center rounded-2xl border-[3px] border-line ${chip}`}
          >
            <ModeIcon name={icon} />
          </div>
          {/* GAME-001: done badge on the icon tile's corner — dark disc +
              white check reads against every chip color, unlike a colored
              badge which would wash out on the gold/red tiles. */}
          {done && (
            <div className="absolute -right-2 -top-2 grid h-6 w-6 place-items-center rounded-full border-[3px] border-line bg-ink-fill">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={3}
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-3 w-3 text-on-fill"
              >
                <path d="M5 13l4 4L19 7" />
              </svg>
            </div>
          )}
        </div>
        {/* PAY-004: tier chip on the opposite corner of the icon row. */}
        {badge && (
          <span
            className={`rounded-full border-[3px] border-line px-3 py-1 font-body text-[11px] font-bold uppercase tracking-[0.14em] ${chip}`}
          >
            {badge}
          </span>
        )}
      </div>
      {kicker && (
        <p className="mt-6 font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
          {kicker}
          {done && " · DONE"}
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
  | "bulb"
  | "mail";

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
  if (name === "mail") {
    // envelope — "a letter arrives, you write back"
    return (
      <svg {...common}>
        <rect x="3" y="5.5" width="18" height="13" rx="2" />
        <path d="m3.5 6.5 8.5 7 8.5-7" />
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

// PAY-002: header coin chip — signed-out sees nothing; signed-in always sees
// the pill (even at 0) so the currency is discoverable.
function PracticeMenuCoinChip() {
  const bal = useCoinBalance();
  if (!bal) return null;
  return <CoinPill balance={bal.balance} nextResetAt={bal.nextResetAt} />;
}
