"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import {
  fetchStats,
  UnauthorizedError,
  type DevelopmentStats,
  type ExerciseKey,
  type ExerciseStat,
  type TopError,
  type FocusPattern,
  type RetiredPattern,
} from "./development/api";

// Development — the learner's own progress dashboard: this week's activity
// and accuracy per exercise, their biggest current errors, what the coach is
// focusing on next, and patterns they've already conquered. Read-only, no
// interaction beyond the entry point back to /practice. Mirrors the
// auth-guarded page-shell pattern from Szenario.tsx (useAuth + redirect +
// header) rather than any drill trainer, since there's nothing to attempt
// here.
const EXERCISE_LABELS: Record<ExerciseKey, string> = {
  satz: "Satzschmiede",
  verbformen: "Past-Tense Verbs",
  sprechen: "Speaking Drills",
  bauteil: "Declension",
  verbindungen: "Feste Verbindungen",
  szenario: "Szenario-Sparring",
};

export default function Development() {
  const { token, ready, signOut } = useAuth();
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
          signOut();
        } else {
          setError(true);
        }
      });
    return () => {
      cancelled = true;
    };
    // signOut is a stable ref — token is the real trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (!ready || !token) {
    return null;
  }

  const noActivity =
    stats !== null &&
    stats.week.attemptsTotal === 0 &&
    stats.prevWeek.attemptsTotal === 0;

  return (
    <div className="relative flex min-h-screen flex-col bg-white text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

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
          <Link
            href="/practice"
            className="font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
          >
            ← Menu
          </Link>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col px-6 py-12">
        <div className="mb-8 text-center">
          <h1 className="font-display text-[28px] font-black tracking-tight text-ink">
            Your Development
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            your german, measured
          </p>
        </div>

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            Couldn&apos;t load your stats — is the backend running?
          </p>
        ) : stats === null ? (
          <p className="text-center font-body text-[12px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
            Loading…
          </p>
        ) : (
          <div className="flex flex-col gap-6">
            <FocusCard focus={stats.focus} />

            {noActivity ? (
              <EmptyActivityCard />
            ) : (
              <>
                <WeekComparisonCard
                  weekTotal={stats.week.attemptsTotal}
                  prevWeekTotal={stats.prevWeek.attemptsTotal}
                  exercises={stats.week.exercises}
                />
                <ErrorsCard
                  todayErrors={stats.today.topErrors}
                  weekErrors={stats.week.topErrors}
                />
                <RetiredCard retired={stats.retired} />
              </>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

// ─── Focus card — the hero. What the coach wants worked on next. ──────────

function FocusCard({ focus }: { focus: FocusPattern[] }) {
  return (
    <section className="rounded-[28px] border-[3px] border-flag-red bg-flag-red-soft p-7">
      <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-flag-red-deep">
        Coach focus
      </p>
      <h2 className="mt-1 font-display text-[22px] font-black tracking-tight text-ink">
        This week, work on:
      </h2>

      {focus.length === 0 ? (
        <p className="mt-4 font-body text-[15px] leading-relaxed text-ink-soft">
          Nothing on the radar — go practice and I&apos;ll find your weak
          spots.
        </p>
      ) : (
        <ul className="mt-5 flex flex-col gap-3">
          {focus.map((f) => (
            <li
              key={f.patternId}
              className="rounded-[20px] border-[3px] border-ink bg-white p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <p className="font-display text-[16px] font-black leading-tight text-ink">
                  {f.label}
                </p>
                <span className="shrink-0 rounded-full border-2 border-ink bg-flag-gold-soft px-2.5 py-0.5 font-body text-[11px] font-bold uppercase tracking-[0.1em] text-ink">
                  {f.count7d > 0
                    ? `${f.count7d}× this week`
                    : `${f.lifetime}× overall`}
                </span>
              </div>
              {f.description && (
                <p className="mt-1.5 font-body text-[14px] leading-relaxed text-ink-soft">
                  {f.description}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ─── This week vs last week — attempts + per-exercise accuracy bars. ──────

function WeekComparisonCard({
  weekTotal,
  prevWeekTotal,
  exercises,
}: {
  weekTotal: number;
  prevWeekTotal: number;
  exercises: ExerciseStat[];
}) {
  return (
    <section className="rounded-[28px] border-[3px] border-ink bg-white p-7">
      <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
        This week vs last week
      </h2>
      <p className="mt-1 font-body text-[13px] font-semibold text-ink-muted">
        {weekTotal} attempts this week · {prevWeekTotal} last week
      </p>

      {exercises.length === 0 ? (
        <p className="mt-5 font-body text-[14px] text-ink-soft">
          No attempts yet this week.
        </p>
      ) : (
        <div className="mt-5 flex flex-col gap-4">
          {exercises.map((ex) => (
            <div key={ex.exercise}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-body text-[14px] font-bold text-ink">
                  {EXERCISE_LABELS[ex.exercise]}
                </span>
                <span className="font-body text-[12px] font-semibold text-ink-muted">
                  {ex.attempts} attempt{ex.attempts === 1 ? "" : "s"}
                </span>
              </div>
              {ex.accuracy === null ? (
                <p className="mt-1.5 font-body text-[12px] font-semibold text-ink-faint">
                  —
                </p>
              ) : (
                <div className="mt-1.5 h-2.5 w-full overflow-hidden rounded-full border-2 border-ink bg-paper">
                  <div
                    className="h-full rounded-full bg-success"
                    style={{
                      width: `${Math.round(ex.accuracy * 100)}%`,
                    }}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ─── Biggest errors — today (if any) then the weekly list. ────────────────

function ErrorsCard({
  todayErrors,
  weekErrors,
}: {
  todayErrors: TopError[];
  weekErrors: TopError[];
}) {
  return (
    <section className="rounded-[28px] border-[3px] border-ink bg-white p-7">
      <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
        Biggest errors
      </h2>

      {todayErrors.length > 0 && (
        <div className="mt-5">
          <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
            Today
          </p>
          <ul className="mt-2.5 flex flex-col gap-2.5">
            {todayErrors.map((e) => (
              <ErrorRow key={`today-${e.patternId}`} error={e} />
            ))}
          </ul>
        </div>
      )}

      <div className="mt-5">
        <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
          This week
        </p>
        {weekErrors.length === 0 ? (
          <p className="mt-2.5 font-body text-[14px] text-ink-soft">
            No errors logged this week — nice.
          </p>
        ) : (
          <ul className="mt-2.5 flex flex-col gap-2.5">
            {weekErrors.map((e) => (
              <ErrorRow key={`week-${e.patternId}`} error={e} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function ErrorRow({ error }: { error: TopError }) {
  return (
    <li className="rounded-[16px] border-[3px] border-ink bg-white px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="font-body text-[14px] font-bold text-ink">
          {error.label}
        </span>
        <span className="shrink-0 rounded-full border-2 border-ink bg-flag-red-soft px-2.5 py-0.5 font-body text-[11px] font-bold uppercase tracking-[0.1em] text-flag-red-deep">
          {error.count}×
        </span>
      </div>
      {error.example && (
        <div className="mt-1.5">
          <p className="font-body text-[13px] text-ink-soft">
            <span className="line-through decoration-flag-red decoration-2">
              {error.example.sentence}
            </span>
          </p>
          <p className="mt-0.5 font-body text-[14px] font-bold text-ink">
            {error.example.corrected}
          </p>
        </div>
      )}
    </li>
  );
}

// ─── Retired patterns — conquered, celebratory chips. Hidden when empty. ──

function RetiredCard({ retired }: { retired: RetiredPattern[] }) {
  if (retired.length === 0) return null;
  return (
    <section className="rounded-[28px] border-[3px] border-ink bg-white p-7">
      <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
        Conquered
      </h2>
      <div className="mt-4 flex flex-wrap gap-2">
        {retired.map((r) => (
          <span
            key={r.patternId}
            className="inline-flex items-center rounded-full border-[3px] border-success bg-success-soft px-3.5 py-1.5 font-body text-[13px] font-bold text-success"
          >
            ✓ {r.label}
          </span>
        ))}
      </div>
    </section>
  );
}

// ─── Global empty state — no attempts this week or last. ──────────────────

function EmptyActivityCard() {
  return (
    <section className="rounded-[28px] border-[3px] border-ink bg-white p-7 text-center">
      <p className="font-display text-[18px] font-black tracking-tight text-ink">
        No activity yet
      </p>
      <p className="mt-2 font-body text-[14px] leading-relaxed text-ink-soft">
        Practice a lesson and your stats will start showing up here.
      </p>
      <Link
        href="/practice"
        className="btn-3d mt-5 inline-flex items-center gap-2 rounded-[16px] border-[3px] border-ink bg-flag-gold px-5 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink"
        style={{ ["--shadow-color"]: "var(--color-ink)" } as React.CSSProperties}
      >
        Go practice
      </Link>
    </section>
  );
}
