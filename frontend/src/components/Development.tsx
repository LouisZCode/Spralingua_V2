"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import {
  fetchStats,
  fetchSessions,
  UnauthorizedError,
  type DevelopmentStats,
  type RecentSession,
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
  // BUG-010: recent conversation sessions — where a user-ended tandem's
  // debrief becomes readable. Non-fatal: load failure just hides the block.
  const [sessions, setSessions] = useState<RecentSession[]>([]);

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

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    fetchSessions(token)
      .then((s) => {
        if (!cancelled) setSessions(s);
      })
      .catch((e) => {
        if (!cancelled && e instanceof UnauthorizedError) signOut();
        // Any other failure: keep [] and hide the block — the stats above
        // are the page's core, session history must not take it down.
      });
    return () => {
      cancelled = true;
    };
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
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <header className="sticky top-0 z-50 border-b-[3px] border-line bg-card/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-2.5">
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
              streak={stats.streak}
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

            {sessions.length > 0 && <RecentSessionsCard sessions={sessions} />}
          </div>
        )}

        {/* MVP-001: every drill that no longer has a card on /practice. It
            sits OUTSIDE the stats conditional on purpose — a backend that
            can't serve stats is exactly when you want to open a drill and
            find out why. Collapsed by default; nothing here is part of the
            learner's path. */}
        <AllExercisesCard />
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
];

function AllExercisesCard() {
  return (
    <details className="mt-10 rounded-[28px] border-[3px] border-ink-faint bg-paper-warm p-6">
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
            <span className="font-body text-[11px] font-semibold text-ink-faint">
              {ex.note}
            </span>
          </Link>
        ))}
      </div>
    </details>
  );
}

// ─── Positives first (DATA-005): what's already conquered + the week's
// effort, before anything that needs work. ────────────────────────────────
function PositiveCard({
  retired,
  weekTotal,
  prevWeekTotal,
  streak,
}: {
  retired: RetiredPattern[];
  weekTotal: number;
  prevWeekTotal: number;
  streak: DevelopmentStats["streak"];
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
              className="inline-flex items-center gap-1.5 rounded-full border-[3px] border-success bg-card px-3.5 py-1.5 font-body text-[13px] font-bold text-ink"
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
      {/* GAME-001: forgiving daily streak. current=0 never shames the reset —
          it falls back to the longest-streak PR, which never decreases. */}
      {(streak.current > 0 || streak.longest > 0) && (
        <p className="mt-2 font-body text-[13px] font-semibold text-ink-soft">
          {streak.current > 0
            ? `🔥 ${streak.current}-day streak · longest ${streak.longest}`
            : `Longest streak: ${streak.longest} days`}
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
              className="rounded-[20px] border-[3px] border-line bg-card p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <p className="font-display text-[16px] font-black leading-tight text-ink">
                  {f.label}
                </p>
                <span className="shrink-0 rounded-full border-2 border-line bg-flag-gold-soft px-2.5 py-0.5 font-body text-[11px] font-bold uppercase tracking-[0.1em] text-ink">
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
    <section className="rounded-[28px] border-[3px] border-line bg-card p-7">
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
    <li className="rounded-[16px] border-[3px] border-line bg-card px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="font-body text-[14px] font-bold text-ink">
          {error.label}
        </span>
        <span className="shrink-0 rounded-full border-2 border-line bg-flag-red-soft px-2.5 py-0.5 font-body text-[11px] font-bold uppercase tracking-[0.1em] text-flag-red-deep">
          {error.count}×
        </span>
      </div>
      {error.example &&
        (() => {
          const ex = error.example;
          // A slip can arrive without a stored correction — show the
          // sentence plain instead of crashing the page on a null diff.
          if (!ex.corrected) {
            return (
              <p className="mt-1.5 font-body text-[13px] text-ink-soft">
                {ex.sentence}
              </p>
            );
          }
          const d = diffTokens(ex.sentence, ex.corrected, { caseInsensitive: true });
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

// ─── Recent sessions (BUG-010 / UI-005 minimal): the readable home of the
// tandem debrief now that a user-ended chat exits straight to the menu.
// Rows expand in place; session_note stays hidden (Lena's private memory,
// same rule as TandemDebriefModal). ───────────────────────────────────────

const LESSON_LABELS: Record<string, string> = {
  tandem: "Tandem · Lena",
  tandem_paul: "Tandem · Paul",
  lesson_zero: "Free conversation",
};

function sessionDate(iso: string): string {
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getDate()}.${d.getMonth() + 1}. · ${hh}:${mm}`;
}

function RecentSessionsCard({ sessions }: { sessions: RecentSession[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <section className="rounded-[28px] border-[3px] border-line bg-card p-7">
      <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
        Recent sessions
      </h2>
      <p className="mt-1 font-body text-[11px] font-semibold uppercase tracking-[0.22em] text-ink-muted">
        your conversation notes live here
      </p>
      <ul className="mt-4 flex flex-col gap-2.5">
        {sessions.map((s) => (
          <SessionRow
            key={s.id}
            session={s}
            open={openId === s.id}
            onToggle={() => setOpenId(openId === s.id ? null : s.id)}
          />
        ))}
      </ul>
    </section>
  );
}

function SessionRow({
  session,
  open,
  onToggle,
}: {
  session: RecentSession;
  open: boolean;
  onToggle: () => void;
}) {
  const patterns = session.debrief?.patterns ?? [];
  const newErrors = session.debrief?.new_errors ?? [];
  const retired = patterns.filter((p) => p.retired);
  const practiced = patterns.filter((p) => p.elicited && !p.retired);
  const hasNotes =
    retired.length > 0 || practiced.length > 0 || newErrors.length > 0;

  return (
    <li className="rounded-[16px] border-[3px] border-line bg-card">
      <button
        type="button"
        onClick={onToggle}
        disabled={!hasNotes}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span className="flex min-w-0 items-baseline gap-3">
          <span className="truncate font-body text-[14px] font-bold text-ink">
            {LESSON_LABELS[session.lessonId] ?? session.lessonId}
          </span>
          <span className="shrink-0 font-body text-[12px] font-semibold text-ink-muted">
            {sessionDate(session.endedAt)}
          </span>
        </span>
        {hasNotes ? (
          <span className="flex shrink-0 items-center gap-2">
            <span className="rounded-full border-2 border-line bg-flag-gold-soft px-2.5 py-0.5 font-body text-[11px] font-bold uppercase tracking-[0.1em] text-ink">
              {newErrors.length > 0
                ? `${newErrors.length} to work on`
                : "notes"}
            </span>
            <span aria-hidden className="font-body text-[12px] font-bold text-ink">
              {open ? "▴" : "▾"}
            </span>
          </span>
        ) : (
          <span className="shrink-0 font-body text-[12px] text-ink-muted">
            no notes
          </span>
        )}
      </button>

      {open && hasNotes && (
        <div className="space-y-3 border-t-2 border-rule px-4 py-3.5">
          {retired.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {retired.map((p) => (
                <span
                  key={p.pattern_id}
                  className="inline-flex items-center gap-1.5 rounded-full border-2 border-success bg-card px-3 py-1 font-body text-[12px] font-bold text-ink"
                >
                  <span className="text-success">✓</span>
                  {p.label} — mastered
                </span>
              ))}
            </div>
          )}

          {practiced.map((p) => {
            const d =
              !p.produced_correctly && p.corrected
                ? diffTokens(p.evidence, p.corrected, { caseInsensitive: true })
                : null;
            return (
              <div key={p.pattern_id} className="font-body text-[13px] leading-snug">
                <p className="font-semibold text-ink">
                  <span
                    aria-hidden
                    className={p.produced_correctly ? "text-success" : "text-flag-gold-deep"}
                  >
                    {p.produced_correctly ? "✓ " : "↻ "}
                  </span>
                  {p.label}
                </p>
                {p.produced_correctly ? (
                  <p className="mt-0.5 text-ink-soft">“{p.evidence}”</p>
                ) : (
                  d && (
                    <p className="mt-0.5 text-ink-soft">
                      “<MarkedText tokens={d.attempt} mark="red" />” →{" "}
                      <span className="font-semibold text-ink">
                        “<MarkedText tokens={d.corrected} mark="green" />”
                      </span>
                    </p>
                  )
                )}
              </div>
            );
          })}

          {newErrors.map((e, i) => {
            const d = e.corrected ? diffTokens(e.sentence, e.corrected, { caseInsensitive: true }) : null;
            return (
              <div key={`${e.pattern_id}-${i}`} className="font-body text-[13px] leading-snug">
                <p className="font-semibold text-ink">{e.label}</p>
                {d ? (
                  <p className="mt-0.5 text-ink-soft">
                    “<MarkedText tokens={d.attempt} mark="red" />” →{" "}
                    <span className="font-semibold text-ink">
                      “<MarkedText tokens={d.corrected} mark="green" />”
                    </span>
                  </p>
                ) : (
                  <p className="mt-0.5 text-ink-soft">“{e.sentence}”</p>
                )}
                {e.note && (
                  <p className="mt-0.5 text-[12px] italic text-ink-muted">{e.note}</p>
                )}
              </div>
            );
          })}
        </div>
      )}
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
    <section className="rounded-[28px] border-[3px] border-line bg-card p-7">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-display text-[18px] font-black tracking-tight text-ink">
          Your attempts
        </h2>
        <div className="inline-flex overflow-hidden rounded-full border-[3px] border-line">
          {(["day", "week"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={`px-3.5 py-1 font-display text-[11px] font-black uppercase tracking-[0.14em] transition-colors ${
                v === view ? "bg-ink-fill text-on-fill" : "bg-card text-ink hover:text-flag-red"
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
