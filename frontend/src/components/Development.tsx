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
  type TopError,
  type FocusPattern,
  type RetiredPattern,
  type SeriesPoint,
} from "./development/api";
import { diffTokens, MarkedText } from "./shared/feedback";

// Development — the learner's own progress dashboard: positives first (what's
// already conquered + this week's effort), then what still needs work — the
// coach's focus, the biggest current errors, and one attempts chart. Read-only,
// no interaction beyond the entry point back to /practice and the chart's
// day/week toggle. Mirrors the auth-guarded page-shell pattern from
// Szenario.tsx (useAuth + redirect + header) rather than any drill trainer,
// since there's nothing to attempt here.

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
            <PositiveCard
              retired={stats.retired}
              weekTotal={stats.week.attemptsTotal}
              prevWeekTotal={stats.prevWeek.attemptsTotal}
            />

            {noActivity ? (
              <EmptyActivityCard />
            ) : (
              <>
                <FocusCard focus={stats.focus} />
                <ErrorsCard errors={stats.week.topErrors.slice(0, 3)} />
                <AttemptsChart series={stats.series} />
              </>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

// ─── Positives first (DATA-005): what's already conquered + the week's
// effort, before anything that needs work. ────────────────────────────────
function PositiveCard({
  retired,
  weekTotal,
  prevWeekTotal,
}: {
  retired: RetiredPattern[];
  weekTotal: number;
  prevWeekTotal: number;
}) {
  return (
    <section className="rounded-[28px] border-[3px] border-success bg-success-soft p-7">
      <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-success">
        Conquered
      </p>
      <h2 className="mt-1 font-display text-[22px] font-black tracking-tight text-ink">
        What you&apos;ve already nailed
      </h2>
      {retired.length === 0 ? (
        <p className="mt-4 font-body text-[15px] leading-relaxed text-ink-soft">
          Every error pattern you retire lands here — keep practicing.
        </p>
      ) : (
        <div className="mt-4 flex flex-wrap gap-2">
          {retired.map((r) => (
            <span
              key={r.patternId}
              className="inline-flex items-center gap-1.5 rounded-full border-[3px] border-success bg-white px-3.5 py-1.5 font-body text-[13px] font-bold text-ink"
            >
              <span className="text-success">✓</span>
              {r.label}
            </span>
          ))}
        </div>
      )}
      {weekTotal > 0 && (
        <p className="mt-4 font-body text-[13px] font-semibold text-ink-soft">
          {weekTotal} attempts this week · {prevWeekTotal} last week
        </p>
      )}
    </section>
  );
}

// ─── Focus card — what the coach wants worked on next. ────────────────────

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

// ─── Biggest errors — the week's top misses, simplified (DATA-005: dropped
// the today/this-week split). ──────────────────────────────────────────────

function ErrorsCard({ errors }: { errors: TopError[] }) {
  return (
    <section className="rounded-[28px] border-[3px] border-ink bg-white p-7">
      <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
        Biggest errors
      </h2>

      {errors.length === 0 ? (
        <p className="mt-4 font-body text-[14px] text-ink-soft">
          No errors logged this week — nice.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-2.5">
          {errors.map((e) => (
            <ErrorRow key={e.patternId} error={e} />
          ))}
        </ul>
      )}
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
      {error.example &&
        (() => {
          const ex = error.example;
          const d = diffTokens(ex.sentence, ex.corrected);
          return (
            <div className="mt-1.5">
              <p className="font-body text-[13px] text-ink-soft">
                <MarkedText tokens={d.attempt} mark="red" />
              </p>
              <p className="mt-0.5 font-body text-[14px] font-bold text-ink">
                <MarkedText tokens={d.corrected} mark="green" />
              </p>
            </div>
          );
        })()}
    </li>
  );
}

// ─── The one progress graph (DATA-005): attempts vs mistakes vs
// first-try-correct, day buckets by default, week buckets one tap away.
// Derived entirely from the 56-day daily series — no second endpoint. ─────
function AttemptsChart({ series }: { series: SeriesPoint[] }) {
  const [view, setView] = useState<"day" | "week">("day");
  const byDate = new Map(series.map((p) => [p.date, p]));

  // Buckets, oldest → newest. Day view: the last 14 UTC days, gaps
  // zero-filled. Week view: the last 8 Monday-anchored weeks summed.
  const buckets: { label: string; attempts: number; mistakes: number; firstTryCorrect: number }[] = [];
  const today = new Date();
  if (view === "day") {
    for (let i = 13; i >= 0; i--) {
      const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
      const key = d.toISOString().slice(0, 10);
      const p = byDate.get(key);
      buckets.push({
        label: `${d.getUTCDate()}.`,
        attempts: p?.attempts ?? 0,
        mistakes: p?.mistakes ?? 0,
        firstTryCorrect: p?.firstTryCorrect ?? 0,
      });
    }
  } else {
    const dow = (today.getUTCDay() + 6) % 7; // Monday = 0
    for (let w = 7; w >= 0; w--) {
      const monday = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - dow - w * 7));
      const bucket = { label: `${monday.getUTCDate()}.${monday.getUTCMonth() + 1}.`, attempts: 0, mistakes: 0, firstTryCorrect: 0 };
      for (let i = 0; i < 7; i++) {
        const d = new Date(monday);
        d.setUTCDate(monday.getUTCDate() + i);
        const p = byDate.get(d.toISOString().slice(0, 10));
        if (p) {
          bucket.attempts += p.attempts;
          bucket.mistakes += p.mistakes;
          bucket.firstTryCorrect += p.firstTryCorrect;
        }
      }
      buckets.push(bucket);
    }
  }

  const max = Math.max(1, ...buckets.map((b) => b.attempts));
  const empty = buckets.every((b) => b.attempts === 0);

  return (
    <section className="rounded-[28px] border-[3px] border-ink bg-white p-7">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
          Your attempts
        </h2>
        <div className="inline-flex overflow-hidden rounded-full border-[3px] border-ink">
          {(["day", "week"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={`px-3.5 py-1 font-display text-[11px] font-black uppercase tracking-[0.14em] transition-colors ${
                v === view ? "bg-ink text-white" : "bg-white text-ink hover:text-flag-red"
              }`}
            >
              {v === "day" ? "Days" : "Weeks"}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
        {(
          [
            ["bg-ink", "Attempts"],
            ["bg-flag-red", "Mistakes"],
            ["bg-success", "First-try correct"],
          ] as const
        ).map(([dot, label]) => (
          <span key={label} className="inline-flex items-center gap-1.5 font-body text-[11px] font-semibold text-ink-soft">
            <span aria-hidden className={`h-2.5 w-2.5 rounded-full ${dot}`} />
            {label}
          </span>
        ))}
      </div>

      {empty ? (
        <p className="mt-5 font-body text-[14px] text-ink-soft">
          No attempts in this window yet.
        </p>
      ) : (
        <div className="mt-5 flex">
          {buckets.map((b, i) => (
            <div key={i} className="flex min-w-0 flex-1 flex-col items-center">
              {/* Every bucket's bar area carries its own border-b segment;
                  with no gap between buckets they join into one continuous
                  baseline, so zero days read as flat ground instead of
                  floating nubs. */}
              <div className="flex h-32 w-full items-end justify-center gap-[3px] border-b-2 border-ink">
                {(
                  [
                    [b.attempts, "bg-ink"],
                    [b.mistakes, "bg-flag-red"],
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
                      title={`${value}`}
                    />
                  )
                )}
              </div>
              {/*   keeps the label slot's height on unlabeled buckets —
                  a bare " " collapses and un-aligns the baselines. */}
              <span className="mt-1.5 font-body text-[9px] font-semibold text-ink-faint">
                {view === "day" ? (i % 2 === 0 ? b.label : " ") : b.label}
              </span>
            </div>
          ))}
        </div>
      )}
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
