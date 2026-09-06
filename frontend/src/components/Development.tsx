"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import {
  fetchStats,
  UnauthorizedError,
  type DevelopmentStats,
  type PeriodStats,
  type RetiredPattern,
  type SeriesPoint,
  type Streak,
  type TopError,
} from "./development/api";
import { diffTokens, MarkedText } from "./shared/feedback";
import { loadError } from "./shared/copy";
import AppHeader from "@/components/shared/AppHeader";

// Development — the learner's own progress dashboard (DEV-002 layout, top to
// bottom): the dev-only drill index (users.role === "developer"), the
// attempts chart with This-Week/Last-Week tiles, the streak hero (current +
// longest, a small pop when today's earn reaches the record), the week's top
// mistakes (neutral, expandable to your-words-vs-correct), the then-vs-now
// progress card, and the retired "conquered" patterns at the end. Read-only,
// no interaction beyond the toggles/accordions and the entry point back to
// /practice. Mirrors the auth-guarded page-shell pattern from Szenario.tsx
// (useAuth + redirect + header) rather than any drill trainer, since there's
// nothing to attempt here.

export default function Development() {
  const { token, user, ready, expireSession } = useAuth();
  const router = useRouter();

  const [stats, setStats] = useState<DevelopmentStats | null>(null); // null = loading
  const [error, setError] = useState(false);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    fetchStats(token)
      .then((s) => {
        if (!cancelled) setStats(s);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof UnauthorizedError) {
          expireSession();
        } else {
          setError(true);
        }
      });
    return () => {
      cancelled = true;
    };
    // expireSession is a stable ref — token is the real trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (!ready || !token) {
    return null;
  }

  // DEV-002: the drill index is a developer-only door — gated on the same
  // users.role flag /practice uses (PracticeMenu's isDev).
  const isDev = user?.role === "developer";

  const noActivity =
    stats !== null &&
    stats.week.attemptsTotal === 0 &&
    stats.prevWeek.attemptsTotal === 0;

  return (
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      {/* APPHDR-001: shared header — the logo targets /practice, the
          signed-in learner's home. */}
      <AppHeader back={{ href: "/practice", label: "← Menu" }} />

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col px-6 py-12">
        <div className="mb-8 text-center">
          <h1 className="font-display text-[28px] font-black tracking-tight text-ink">
            Your Development
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            your german, measured
          </p>
        </div>

        {/* MVP-001, DEV-002: every drill that no longer has a card on
            /practice — now the first thing on the page, developer-only
            (users.role === "developer"). Still OUTSIDE the stats conditional
            on purpose: a backend that can't serve stats is exactly when you
            want to open a drill and find out why. Collapsed by default. */}
        {isDev && <AllExercisesCard />}

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            {loadError("your stats")}
          </p>
        ) : stats === null ? (
          <p className="text-center font-body text-[12px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
            Loading…
          </p>
        ) : (
          <div className="flex flex-col gap-6">
            {noActivity ? (
              <>
                <StreakCard streak={stats.streak} />
                <EmptyActivityCard />
              </>
            ) : (
              <>
                <AttemptsCard
                  week={stats.week}
                  prevWeek={stats.prevWeek}
                  series={stats.series}
                />
                <StreakCard streak={stats.streak} />
                <TopMistakesCard errors={stats.week.topErrors.slice(0, 3)} />
                <ProgressCard retired={stats.retired} />
                <ConqueredCard retired={stats.retired} />
              </>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

// ─── Dev-only drill index (MVP-001). ──────────────────────────────────────
//
// /practice shows four exercises. The rest still exist, still run, and are
// still reachable — the single-grammar ones are how the Flow is built, so
// they have to stay individually testable. This is that door.
const HIDDEN_EXERCISES: { href: string; name: string; note: string }[] = [
  { href: "/faelle", name: "Fälle", note: "case cluster — in Flow" },
  { href: "/satzbau", name: "Satzbau", note: "clause builder — in Flow" },
  { href: "/bauteil", name: "Bauteil-Sätze", note: "declension — in Flow" },
  { href: "/verbindungen", name: "Feste Verbindungen", note: "chunks — in Flow" },
  { href: "/zeitfaerbung", name: "Zeitfärbung", note: "Präteritum — in Flow" },
  { href: "/verbformen", name: "Verbformen", note: "past forms — in Flow" },
  { href: "/genus", name: "Artikel-Anker", note: "der/die/das — in Flow" },
  { href: "/sprechen", name: "Sprechen", note: "speaking drills — in Flow" },
  { href: "/szenario", name: "Szenario-Sparring", note: "structure coach — in Flow" },
  { href: "/learn", name: "Conversation Practice", note: "dormant" },
  { href: "/interview", name: "Interview", note: "personal audio pool — INTV-003" },
  { href: "/teacher", name: "Clara — Teacher", note: "direct door — behavior testing" },
];

function AllExercisesCard() {
  return (
    <details className="mb-6 rounded-[28px] border-[3px] border-ink-faint bg-paper-warm p-6">
      <summary className="cursor-pointer list-none font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
        All exercises · dev
      </summary>
      <p className="mt-3 font-body text-[13px] leading-relaxed text-ink-soft">
        Every drill, including the ones without a card on the practice menu.
        The ones marked <em>in Flow</em> are what the Flow deals from.
      </p>
      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {HIDDEN_EXERCISES.map((ex) => (
          <Link
            key={ex.href}
            href={ex.href}
            className="flex items-baseline justify-between gap-3 rounded-2xl border-[3px] border-line bg-card px-4 py-2.5 transition-colors hover:bg-flag-gold-soft"
          >
            <span className="font-display text-[14px] font-black tracking-tight text-ink">
              {ex.name}
            </span>
            <span className="font-body text-[11px] font-semibold text-ink-muted">
              {ex.note}
            </span>
          </Link>
        ))}
      </div>
    </details>
  );
}

// ─── Streak hero (DEV-002): current + longest day streak, its own space,
// zeros stay calm. When today's earn has caught up with the all-time record
// the number pops once — CSS only. ─────────────────────────────────────────
// STUDY-001: "2 months, 1 week, and 3 days" from the learner's first-activity
// anchor. Calendar-accurate month math (Jan 31 → Mar 1 reads as "1 month",
// not "4 weeks and 1 day"), zero segments dropped, all-zero → a day-one
// greeting instead of "0 months". null → caller hides the line entirely.
function studyDurationText(since: string | null): string | null {
  if (!since) return null;
  const start = new Date(since);
  if (Number.isNaN(start.getTime())) return null;
  const now = new Date();
  const sy = start.getUTCFullYear();
  const sm = start.getUTCMonth();
  const sd = start.getUTCDate();
  const ty = now.getUTCFullYear();
  const tm = now.getUTCMonth();
  const td = now.getUTCDate();
  let months = (ty - sy) * 12 + (tm - sm);
  if (td < sd) months -= 1; // this month's anchor day hasn't come around yet
  if (months < 0) months = 0;
  // Remainder days: today minus (start shifted forward by the full months).
  // Date.UTC normalizes overflow day-of-months (Jan 31 + 1mo → Mar 3), and a
  // negative remainder just means the month boundary eats those days.
  const anchor = new Date(Date.UTC(sy, sm + months, sd));
  const DAY = 86400000;
  let rem = Math.floor(
    (Date.UTC(ty, tm, td) -
      Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), anchor.getUTCDate())) /
      DAY,
  );
  if (rem < 0) rem = 0;
  if (months === 0 && rem === 0) return "…well, you just started — day 1!";
  const weeks = Math.floor(rem / 7);
  const days = rem % 7;
  const segs: string[] = [];
  if (months > 0) segs.push(`${months} month${months === 1 ? "" : "s"}`);
  if (weeks > 0) segs.push(`${weeks} week${weeks === 1 ? "" : "s"}`);
  if (days > 0) segs.push(`${days} day${days === 1 ? "" : "s"}`);
  if (segs.length === 1) return segs[0];
  if (segs.length === 2) return `${segs[0]} and ${segs[1]}`;
  return `${segs[0]}, ${segs[1]}, and ${segs[2]}`;
}

function StreakCard({ streak }: { streak: Streak }) {
  // GAME-001: longest is a permanent PR that never decreases, so
  // current === longest exactly while the live streak is AT its record —
  // that's the moment worth celebrating, and only if today is actually
  // earned (a record matched without today's practice is just history).
  const atRecord =
    streak.practicedToday && streak.current > 0 && streak.current >= streak.longest;
  const studyText = studyDurationText(streak.studyingSince);
  return (
    <section className="rounded-[28px] border-[3px] border-line bg-card p-7 text-center">
      <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
        Day streak
      </p>
      <div className="mt-4 flex items-end justify-center gap-12">
        <div>
          <p
            className={`font-display text-[56px] font-black leading-none tracking-tight text-ink ${
              atRecord ? "animate-streak-pop" : ""
            }`}
          >
            {streak.current}
          </p>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-ink-muted">
            🔥 current
          </p>
        </div>
        <div className="border-l-2 border-rule pl-12">
          <p className="font-display text-[56px] font-black leading-none tracking-tight text-ink-muted">
            {streak.longest}
          </p>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-ink-muted">
            🏆 longest
          </p>
        </div>
      </div>
      {atRecord ? (
        <p className="mt-4 font-body text-[13px] font-bold text-flag-gold-deep">
          New record — keep it alive tomorrow!
        </p>
      ) : streak.current === 0 ? (
        <p className="mt-4 font-body text-[13px] text-ink-soft">
          Complete {streak.modesRequired} practice modes in a day to start one.
        </p>
      ) : streak.practicedToday ? (
        <p className="mt-4 font-body text-[13px] text-ink-soft">
          Today is locked in.
        </p>
      ) : (
        <p className="mt-4 font-body text-[13px] text-ink-soft">
          {streak.modesToday.length} of {streak.modesRequired} modes done today —
          don&apos;t lose it.
        </p>
      )}
      {/* STUDY-001: the long-game counter — keeps counting as long as they
          study, independent of the streak above. */}
      {studyText && (
        <p className="mt-4 border-t-2 border-rule pt-4 font-body text-[13px] text-ink-soft">
          You have been studying German for{" "}
          <span className="font-bold text-ink">{studyText}</span>
        </p>
      )}
    </section>
  );
}

// ─── Conquered (DEV-002): the last few retired patterns as quiet proof of
// work, at the end of the page. Replaces PositiveCard's pill cloud — no
// green box, just the facts, plus one recent sentence you got right. ───────
function ConqueredCard({ retired }: { retired: RetiredPattern[] }) {
  const shown = retired.slice(0, 3);
  return (
    <section className="rounded-[28px] border-[3px] border-line bg-card p-7">
      <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
        Conquered
      </h2>
      <p className="mt-1 font-body text-[11px] font-semibold uppercase tracking-[0.22em] text-ink-muted">
        patterns you&apos;ve retired
      </p>
      {shown.length === 0 ? (
        <p className="mt-4 font-body text-[14px] text-ink-soft">
          Every error pattern you retire lands here — keep practicing.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-2">
          {shown.map((r) => (
            <li
              key={r.patternId}
              className="flex items-center gap-2.5 rounded-2xl border-[3px] border-line bg-paper-warm px-4 py-2.5"
            >
              <span className="shrink-0 text-success">✓</span>
              <span className="font-body text-[14px] font-bold text-ink">
                {r.label}
              </span>
              {r.lastExample && (
                <span className="ml-auto min-w-0 truncate font-body text-[12px] italic text-ink-muted">
                  “{r.lastExample.sentence}”
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ─── Top mistakes (DEV-002): the week's biggest misses — no alarm-red box,
// just information. Each row expands to the learner's own sentence vs the
// correct form. Replaces the old red FocusCard + ErrorsCard pair. ──────────

function TopMistakesCard({ errors }: { errors: TopError[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <section className="rounded-[28px] border-[3px] border-line bg-card p-7">
      <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
        Top mistakes
      </h2>
      <p className="mt-1 font-body text-[11px] font-semibold uppercase tracking-[0.22em] text-ink-muted">
        where your german still slips
      </p>
      {errors.length === 0 ? (
        <p className="mt-4 font-body text-[14px] text-ink-soft">
          No graded slips logged this week — nice.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-2.5">
          {errors.map((e) => (
            <MistakeRow
              key={e.patternId}
              error={e}
              open={openId === e.patternId}
              onToggle={() =>
                setOpenId(openId === e.patternId ? null : e.patternId)
              }
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function MistakeRow({
  error,
  open,
  onToggle,
}: {
  error: TopError;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <li className="rounded-[16px] border-[3px] border-line bg-card">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span className="font-body text-[14px] font-bold text-ink">
          {error.label}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {/* Neutral count pill — the count is information, not a verdict. */}
          <span className="rounded-full border-2 border-line bg-paper-warm px-2.5 py-0.5 font-body text-[11px] font-bold uppercase tracking-[0.1em] text-ink">
            {error.count}×
          </span>
          <span aria-hidden className="font-body text-[12px] font-bold text-ink">
            {open ? "▴" : "▾"}
          </span>
        </span>
      </button>
      {open && (
        <div className="space-y-1.5 border-t-2 border-rule px-4 py-3.5">
          {error.example ? (
            <MistakeDiff
              sentence={error.example.sentence}
              corrected={error.example.corrected}
            />
          ) : (
            <p className="font-body text-[13px] text-ink-soft">
              No example stored for this one yet.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

function MistakeDiff({
  sentence,
  corrected,
}: {
  sentence: string;
  corrected: string | null;
}) {
  // A slip can arrive without a stored correction — show the sentence plain
  // instead of crashing the page on a null diff.
  if (!corrected) {
    return <p className="font-body text-[13px] text-ink-soft">{sentence}</p>;
  }
  const d = diffTokens(sentence, corrected, { caseInsensitive: true });
  return (
    <>
      <p className="font-body text-[13px] text-ink-soft">
        <span className="font-semibold text-ink-muted">You wrote: </span>
        <MarkedText tokens={d.attempt} mark="red" />
      </p>
      <p className="font-body text-[14px] font-bold text-ink">
        <span className="font-semibold text-success">Correct: </span>
        <MarkedText tokens={d.corrected} mark="green" />
      </p>
    </>
  );
}

// ─── Progress — then vs now (DEV-002): for the patterns you've retired, the
// ledger's oldest recorded slip next to how you handle it now — the journey
// told with the learner's own sentences, not a green trophy box. Hidden when
// the ledger holds no full example pairs. ──────────────────────────────────
function ProgressCard({ retired }: { retired: RetiredPattern[] }) {
  const entries = retired.filter((r) => r.firstExample && r.lastExample);
  if (entries.length === 0) return null;
  return (
    <section className="rounded-[28px] border-[3px] border-line bg-card p-7">
      <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
        Progress
      </h2>
      <p className="mt-1 font-body text-[11px] font-semibold uppercase tracking-[0.22em] text-ink-muted">
        how far you&apos;ve come
      </p>
      <ul className="mt-4 flex flex-col gap-4">
        {entries.slice(0, 3).map((r) => {
          const first = r.firstExample;
          const last = r.lastExample;
          if (!first || !last) return null;
          const old = diffTokens(first.sentence, first.corrected, {
            caseInsensitive: true,
          });
          const current = diffTokens(last.sentence, last.corrected, {
            caseInsensitive: true,
          });
          return (
            <li key={r.patternId}>
              <p className="font-display text-[15px] font-black leading-tight text-ink">
                {r.label}
              </p>
              <p className="mt-1.5 font-body text-[13px] leading-relaxed text-ink-soft">
                <span className="font-semibold text-ink-muted">Back then: </span>
                “<MarkedText tokens={old.attempt} mark="red" />”
              </p>
              <p className="mt-1 font-body text-[13px] leading-relaxed text-ink-soft">
                <span className="font-semibold text-ink-muted">Now: </span>“
                <MarkedText tokens={current.corrected} mark="green" />”
              </p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ─── Your attempts (DEV-002): attempts vs mistakes vs first-try-correct,
// weeks first ("Week 1 Aug"-style Monday-anchored labels), weekday names in
// the day view, This-Week/Last-Week stat tiles with a numbers/percent toggle,
// and a paged window (4 weeks / 7 days at a time, ‹ › to walk the range).
// Derived entirely from the 56-day daily series — no second endpoint. ──────
const MONTHS_SHORT = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];
const WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// Which 1-based Monday-anchored week of its month the bucket's Monday falls
// in → "Week 1 Aug".
function weekBucketLabel(monday: Date): string {
  return `Week ${
    Math.floor((monday.getUTCDate() - 1) / 7) + 1
  } ${MONTHS_SHORT[monday.getUTCMonth()]}`;
}

// Graded correct share as a whole percent — null when nothing was graded,
// so a fresh week reads as "—" instead of a fake 0%.
function gradedPct(correct: number, incorrect: number): number | null {
  const graded = correct + incorrect;
  return graded === 0 ? null : Math.round((correct / graded) * 100);
}

// Bar tooltip in the chosen format: percentages of the graded split, or the
// raw tally. A bucket with attempts but nothing graded says so instead of
// inventing a 0%.
function barTitle(
  fmt: "counts" | "percent",
  label: string,
  count: number,
  pc: number | null,
): string {
  if (pc === null) return "no graded attempts";
  return fmt === "percent" ? `${pc}% ${label}` : `${count} ${label}`;
}

// "24 Aug – 30 Aug" caption for the paged window (a single day reads as just
// "24 Aug"). Week windows caption through their Sunday.
function rangeCaption(first: Date, last: Date, view: "week" | "day"): string {
  const end = new Date(last);
  if (view === "week") end.setUTCDate(end.getUTCDate() + 6);
  const a = `${first.getUTCDate()} ${MONTHS_SHORT[first.getUTCMonth()]}`;
  const b = `${end.getUTCDate()} ${MONTHS_SHORT[end.getUTCMonth()]}`;
  return a === b ? a : `${a} – ${b}`;
}

// Tile numbers for one rolling week: only graded exercises carry a
// correct/incorrect split (szenario rows are accuracy-null coach rounds) —
// ungraded attempts still count toward the total but can't be split.
function weekTileNumbers(s: PeriodStats) {
  let graded = 0;
  let correct = 0;
  for (const e of s.exercises) {
    if (e.accuracy !== null) {
      graded += e.attempts;
      correct += e.correct;
    }
  }
  return { total: s.attemptsTotal, correct, incorrect: graded - correct };
}

function AttemptsCard({
  week,
  prevWeek,
  series,
}: {
  week: PeriodStats;
  prevWeek: PeriodStats;
  series: SeriesPoint[];
}) {
  const [view, setView] = useState<"week" | "day">("week");
  // Tiles + bar tooltips as raw tallies or as the graded correct/incorrect
  // split in percent — some learners read "78% correct" better than "34/44".
  const [fmt, setFmt] = useState<"counts" | "percent">("counts");
  // Paged window: 0 = the newest page, each step back shows one older
  // WINDOW-sized slice. Clamped at render so a stale index (e.g. after a new
  // day rolls in) can never point past the data.
  const [page, setPage] = useState(0);
  const WINDOW = view === "week" ? 4 : 7;
  const byDate = new Map(series.map((p) => [p.date, p]));
  const DAY = 86400000;
  const today = new Date();
  // Oldest day we hold data for (the 56-day series); falls back to today so
  // a brand-new user still renders one window of flat zero ground.
  const oldest = new Date(
    series.length > 0
      ? `${series[0].date}T00:00:00Z`
      : Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()),
  );
  const buckets: {
    label: string;
    date: Date;
    attempts: number;
    mistakes: number;
    firstTryCorrect: number;
  }[] = [];
  if (view === "day") {
    const last = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
    for (
      let t = Date.UTC(oldest.getUTCFullYear(), oldest.getUTCMonth(), oldest.getUTCDate());
      t <= last;
      t += DAY
    ) {
      const d = new Date(t);
      const p = byDate.get(d.toISOString().slice(0, 10));
      buckets.push({
        label: WEEKDAYS_SHORT[(d.getUTCDay() + 6) % 7], // weekday name, resets each Monday
        date: d,
        attempts: p?.attempts ?? 0,
        mistakes: p?.mistakes ?? 0,
        firstTryCorrect: p?.firstTryCorrect ?? 0,
      });
    }
  } else {
    // Monday-anchored weeks from the oldest held week through the current one.
    const dow = (today.getUTCDay() + 6) % 7; // Monday = 0
    const lastMonday = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - dow);
    const oldestMonday =
      Date.UTC(oldest.getUTCFullYear(), oldest.getUTCMonth(), oldest.getUTCDate()) -
      ((oldest.getUTCDay() + 6) % 7) * DAY;
    for (let t = oldestMonday; t <= lastMonday; t += 7 * DAY) {
      const monday = new Date(t);
      const bucket = {
        label: weekBucketLabel(monday),
        date: monday,
        attempts: 0,
        mistakes: 0,
        firstTryCorrect: 0,
      };
      for (let i = 0; i < 7; i++) {
        const p = byDate.get(new Date(t + i * DAY).toISOString().slice(0, 10));
        if (p) {
          bucket.attempts += p.attempts;
          bucket.mistakes += p.mistakes;
          bucket.firstTryCorrect += p.firstTryCorrect;
        }
      }
      buckets.push(bucket);
    }
  }

  // End-aligned window: page 0 ends at the newest bucket, each page back
  // steps one WINDOW further into the past. The oldest page may be partial —
  // that's the honest edge of the data.
  const maxPage = Math.max(0, Math.floor((buckets.length - 1) / WINDOW));
  const paged = Math.min(page, maxPage);
  const end = buckets.length - paged * WINDOW;
  const visible = buckets.slice(Math.max(0, end - WINDOW), end);

  const max = Math.max(1, ...visible.map((b) => b.attempts));
  const empty = visible.every((b) => b.attempts === 0);

  return (
    <section className="rounded-[28px] border-[3px] border-line bg-card p-7">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
          Your attempts
        </h2>
        <div className="flex items-center gap-2">
          {/* Numbers vs % — % shows the graded correct/incorrect share;
              numbers stay the raw tallies. Sits left of the Weeks/Days pair. */}
          <div className="inline-flex overflow-hidden rounded-full border-[3px] border-line">
            {(["counts", "percent"] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFmt(f)}
                aria-pressed={f === fmt}
                className={`px-3.5 py-1 font-display text-[11px] font-black uppercase tracking-[0.14em] transition-colors ${
                  f === fmt ? "bg-ink-fill text-on-fill" : "bg-card text-ink hover:text-flag-red"
                }`}
              >
                {f === "percent" ? "%" : "Numbers"}
              </button>
            ))}
          </div>
          <div className="inline-flex overflow-hidden rounded-full border-[3px] border-line">
            {(["week", "day"] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => {
                  setView(v);
                  setPage(0); // the window size changes — snap back to newest
                }}
                className={`px-3.5 py-1 font-display text-[11px] font-black uppercase tracking-[0.14em] transition-colors ${
                  v === view ? "bg-ink-fill text-on-fill" : "bg-card text-ink hover:text-flag-red"
                }`}
              >
                {v === "day" ? "Days" : "Weeks"}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
        {(
          [
            ["bg-ink-fill", "Attempts"],
            ["bg-flag-red-fill", "Mistakes"],
            ["bg-success", "First-try correct"],
          ] as const
        ).map(([dot, label]) => (
          <span key={label} className="inline-flex items-center gap-1.5 font-body text-[11px] font-semibold text-ink-soft">
            <span aria-hidden className={`h-2.5 w-2.5 rounded-full ${dot}`} />
            {label}
          </span>
        ))}
      </div>

      {/* DEV-002: the week-over-week numbers the old one-liner used to cram
          into a sentence — attempts big, correct/incorrect split below. */}
      <div className="mt-5 grid grid-cols-2 gap-3">
        {(
          [
            ["This week", week],
            ["Last week", prevWeek],
          ] as const
        ).map(([heading, s]) => {
          const n = weekTileNumbers(s);
          const pc = gradedPct(n.correct, n.incorrect);
          const ungraded = n.total - (n.correct + n.incorrect);
          return (
            <div
              key={heading}
              className="rounded-[20px] border-[3px] border-line bg-paper-warm p-4 text-center"
            >
              <p className="font-body text-[10px] font-bold uppercase tracking-[0.22em] text-ink-muted">
                {heading}
              </p>
              {/* The headline is always the attempt count — only the
                  correct/incorrect split below flips to percentages. */}
              <p className="mt-1 font-display text-[34px] font-black leading-none tracking-tight text-ink">
                {n.total}
              </p>
              <p className="font-body text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-muted">
                attempts
              </p>
              <div className="mt-2 flex justify-center gap-4 font-body text-[12px] font-bold">
                {fmt === "percent" ? (
                  <>
                    <span className="text-success">
                      ✓ {pc === null ? "—" : `${pc}%`} correct
                    </span>
                    <span className="text-flag-red-deep">
                      ✗ {pc === null ? "—" : `${100 - pc}%`} incorrect
                    </span>
                  </>
                ) : (
                  <>
                    <span className="text-success">✓ {n.correct} correct</span>
                    <span className="text-flag-red-deep">
                      ✗ {n.incorrect} incorrect
                    </span>
                  </>
                )}
              </div>
              {/* Coach rounds have no right/wrong answer — in percent mode
                  they'd silently vanish from the split, so own them. */}
              {fmt === "percent" && ungraded > 0 && (
                <p className="mt-1 font-body text-[10px] font-semibold text-ink-muted">
                  + {ungraded} ungraded
                </p>
              )}
            </div>
          );
        })}
      </div>

      {/* Window nav — only once there's more than one window to walk. */}
      {buckets.length > WINDOW && visible.length > 0 && (
        <div className="mt-5 flex items-center justify-center gap-3">
          <button
            type="button"
            aria-label="Show older"
            disabled={paged >= maxPage}
            onClick={() => setPage(paged + 1)}
            className="inline-flex h-7 w-7 items-center justify-center rounded-full border-[3px] border-line font-display text-[14px] font-black text-ink transition-colors hover:text-flag-red disabled:opacity-30 disabled:hover:text-ink"
          >
            ‹
          </button>
          <span className="font-body text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-muted">
            {rangeCaption(visible[0].date, visible[visible.length - 1].date, view)}
          </span>
          <button
            type="button"
            aria-label="Show newer"
            disabled={paged === 0}
            onClick={() => setPage(paged - 1)}
            className="inline-flex h-7 w-7 items-center justify-center rounded-full border-[3px] border-line font-display text-[14px] font-black text-ink transition-colors hover:text-flag-red disabled:opacity-30 disabled:hover:text-ink"
          >
            ›
          </button>
        </div>
      )}

      {empty ? (
        <p className="mt-5 font-body text-[14px] text-ink-soft">
          No attempts in this window yet.
        </p>
      ) : (
        <div className="mt-5 flex">
          {visible.map((b, i) => {
            // Percent-mode tooltips: bars stay volume-proportional, hover
            // reads as the graded share of this bucket.
            const pcB = gradedPct(b.firstTryCorrect, b.mistakes);
            const titles = [
              `${b.attempts} attempts`,
              barTitle(
                fmt,
                "incorrect",
                b.mistakes,
                pcB === null ? null : 100 - pcB,
              ),
              barTitle(fmt, "correct", b.firstTryCorrect, pcB),
            ];
            return (
              <div key={i} className="flex min-w-0 flex-1 flex-col items-center">
                {/* Every bucket's bar area carries its own border-b segment;
                    with no gap between buckets they join into one continuous
                    baseline, so zero days read as flat ground instead of
                    floating nubs. */}
                <div className="flex h-32 w-full items-end justify-center gap-[3px] border-b-2 border-line">
                  {(
                    [
                      [b.attempts, "bg-ink-fill"],
                      [b.mistakes, "bg-flag-red-fill"],
                      [b.firstTryCorrect, "bg-success"],
                    ] as const
                  ).map(([value, color], j) =>
                    value === 0 ? null : (
                      <div
                        key={j}
                        className={`w-2 rounded-t-[3px] ${color}`}
                        style={{
                          height: `${Math.max(5, Math.round((value / max) * 122))}px`,
                        }}
                        title={titles[j]}
                      />
                    ),
                  )}
                </div>
                <span className="mt-1.5 whitespace-nowrap font-body text-[9px] font-semibold text-ink-muted">
                  {b.label}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// ─── Global empty state — no attempts this week or last. ──────────────────

function EmptyActivityCard() {
  return (
    <section className="rounded-[28px] border-[3px] border-line bg-card p-7 text-center">
      <p className="font-display text-[18px] font-black tracking-tight text-ink">
        No activity yet
      </p>
      <p className="mt-2 font-body text-[14px] leading-relaxed text-ink-soft">
        Practice a lesson and your stats will start showing up here.
      </p>
      <Link
        href="/practice"
        className="btn-3d mt-5 inline-flex items-center gap-2 rounded-[16px] border-[3px] border-line bg-flag-gold px-5 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink-fixed"
        style={{ ["--shadow-color"]: "var(--color-line)" } as React.CSSProperties}
      >
        Go practice
      </Link>
    </section>
  );
}
