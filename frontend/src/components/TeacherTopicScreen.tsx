"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useAuth } from "./auth/AuthContext";
import { fetchStats, UnauthorizedError, type FocusPattern } from "./development/api";
import { TEACHER_NAME } from "./shared/teacher";
import { HTTP_BASE } from "@/lib/api";

const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

type TeacherBal = { tier: string; limit: number; used: number; remaining: number; nextResetAt: string; bypass?: boolean } | null;

export default function TeacherTopicScreen({
  onStart,
}: {
  onStart: (topic: string, patternId?: string) => void;
}) {
  const { token, signOut, user } = useAuth();
  const isDeveloper = user?.role === "developer";
  const [bal, setBal] = useState<TeacherBal>(null);
  useEffect(() => {
    if (!token) return;
    let alive = true;
    fetch(`${HTTP_BASE}/teacher/balance`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (alive && j) setBal(j); })
      .catch(() => {});
    return () => { alive = false; };
  }, [token]);

  const limit = bal?.limit ?? (isDeveloper ? 99 : user?.tier === "premium" ? 3 : user?.tier === "basic" ? 1 : 0);
  const used = bal?.used ?? 0;
  const remaining = bal?.remaining ?? Math.max(0, limit - used);
  const isFreeLocked = !isDeveloper && (user?.tier === "free" || limit === 0);
  const exhausted = !isDeveloper && remaining <= 0;

  const [focus, setFocus] = useState<FocusPattern[]>([]);
  const [focusLoaded, setFocusLoaded] = useState(false);
  const [custom, setCustom] = useState<string>("");
  const [selectedCard, setSelectedCard] = useState<FocusPattern | null>(null);

  useEffect(() => {
    if (!token) return;
    let alive = true;
    fetchStats(token)
      .then((stats) => {
        if (!alive) return;
        setFocus(stats.focus);
        setFocusLoaded(true);
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) signOut();
      });
    return () => { alive = false; };
  }, [token, signOut]);

  // Cold-start slice: an empty ledger means an empty `stats.focus` — fetch
  // the curated starters ONLY once we know that for certain (gated on
  // focusLoaded, not just focus.length, so this never fires speculatively
  // while the real focus fetch is still in flight).
  const [starters, setStarters] = useState<FocusPattern[]>([]);
  useEffect(() => {
    if (!token || !focusLoaded || focus.length > 0) return;
    let alive = true;
    fetch(`${HTTP_BASE}/teacher/starters`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!alive || !j?.starters) return;
        const mapped: FocusPattern[] = (
          j.starters as { pattern_id: string; label: string; description: string }[]
        ).map((s) => ({
          patternId: s.pattern_id,
          label: s.label,
          description: s.description,
          count7d: 0,
          lifetime: 0,
        }));
        setStarters(mapped);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [token, focusLoaded, focus.length]);

  const effectiveTopic = useMemo(() => selectedCard?.label ?? custom.trim(), [selectedCard, custom]);
  const [primary, ...rest] = focus;

  return (
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50" />
      <header className="sticky top-0 z-50 border-b-[3px] border-line bg-card/85 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link href="/practice" className="flex items-center gap-2.5">
            <Image src="/mascot/raven.png" alt="Spralingua raven mascot" width={40} height={40} priority className="mascot-keyline h-9 w-9 select-none" />
            <span className="font-display text-[22px] font-black tracking-tight text-ink">Spralingua</span>
          </Link>
          <Link href="/practice" className="font-body text-[13px] font-bold text-ink-soft hover:text-ink">← All modes</Link>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-14">
        <div className="rise-in">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">{TEACHER_NAME} · Grammar Explained</p>
          <h1 className="mt-3 font-display text-[clamp(28px,4.6vw,44px)] font-black leading-[1.03] tracking-tight text-ink">What don&apos;t you understand?</h1>
          <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
            {TEACHER_NAME} explains in English, one thing at a time. Pick a structure you&apos;ve been slipping on lately, or ask about anything else.
          </p>
        </div>

        {/* Daily allowance note — replaces coin length picker */}
        <div className="rise-in mt-4" style={{ animationDelay: "40ms" }}>
          {isDeveloper ? (
            <p className="font-body text-[12px] text-ink-muted">🪙 developer · unlimited talks</p>
          ) : isFreeLocked ? (
            <p className="font-body text-[12px] text-ink-muted">Clara is included with Basic (1/day) and Premium (3/day) · resets 05:00</p>
          ) : exhausted ? (
            <p className="font-body text-[12px] font-semibold text-flag-red-deep">Daily limit reached — {used}/{limit} today · resets 05:00</p>
          ) : (
            <p className="font-body text-[12px] text-ink-muted">{remaining} of {limit} talk{limit !== 1 ? "s" : ""} left today · {used}/{limit} used · resets 05:00</p>
          )}
        </div>

        {primary && (
          <div className="rise-in mt-9" style={{ animationDelay: "80ms" }}>
            <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">Your biggest slip lately</p>
            <div className="mt-3">
              <FocusCard focus={primary} selected={selectedCard?.patternId === primary.patternId} onSelect={() => setSelectedCard((prev) => (prev?.patternId === primary.patternId ? null : primary))} size="large" />
            </div>
            {rest.length > 0 && (
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                {rest.map((f) => (
                  <FocusCard key={f.patternId} focus={f} selected={selectedCard?.patternId === f.patternId} onSelect={() => setSelectedCard((prev) => (prev?.patternId === f.patternId ? null : f))} size="normal" />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Cold-start slice: no ledger yet — offer level-typical starters
            instead of silently dropping the whole picker block. */}
        {!primary && starters.length > 0 && (
          <div className="rise-in mt-9" style={{ animationDelay: "80ms" }}>
            <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">Good starting points</p>
            <p className="mt-1 font-body text-[13px] text-ink-soft">These get personalized as you practice — after a few exercises, {TEACHER_NAME} focuses on your own mistakes.</p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {starters.map((s) => (
                <FocusCard key={s.patternId} focus={s} selected={selectedCard?.patternId === s.patternId} onSelect={() => setSelectedCard((prev) => (prev?.patternId === s.patternId ? null : s))} size="normal" />
              ))}
            </div>
          </div>
        )}

        <div className="rise-in mt-7" style={{ animationDelay: "200ms" }}>
          <label htmlFor="teacher-custom-topic" className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">Or ask about anything else…</label>
          <input id="teacher-custom-topic" type="text" value={custom} onChange={(e) => { setCustom(e.target.value); setSelectedCard(null); }} placeholder="e.g. why is it 'dem' and not 'den'?" maxLength={120} className="mt-3 w-full rounded-2xl border-[3px] border-line bg-card px-5 py-3.5 font-body text-[16px] text-ink placeholder:text-ink-muted focus:outline-none focus:ring-4 focus:ring-flag-gold-soft" />
        </div>

        {isFreeLocked && (
          <div className="rise-in mt-7 rounded-2xl border-[3px] border-line bg-flag-gold-soft px-6 py-5" style={{ animationDelay: "240ms" }}>
            <p className="font-display text-[16px] font-black text-ink">Clara is a Basic feature</p>
            <p className="mt-1 font-body text-[14px] text-ink-soft">Upgrade to chat with Clara — your grammar teacher who explains in English.</p>
            <Link href="/pricing" className="btn-3d mt-4 inline-flex rounded-2xl border-[3px] border-line bg-card px-5 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink" style={inkShadow}>See pricing →</Link>
          </div>
        )}

        {exhausted && !isFreeLocked && (
          <div className="rise-in mt-7 rounded-2xl border-[3px] border-line bg-paper-warm px-6 py-5" style={{ animationDelay: "240ms" }}>
            <p className="font-display text-[16px] font-black text-ink">All talks used today</p>
            <p className="mt-1 font-body text-[14px] text-ink-soft">You&apos;ve used {used} of {limit} today. Come back after 05:00 — or upgrade for more.</p>
            <Link href="/pricing" className="btn-3d mt-4 inline-flex rounded-2xl border-[3px] border-line bg-card px-5 py-2.5 font-display text-[13px] font-black uppercase tracking-[0.14em] text-ink" style={inkShadow}>See pricing →</Link>
          </div>
        )}

        <div className="rise-in mt-9 flex flex-col items-start gap-4" style={{ animationDelay: "260ms" }}>
          {isFreeLocked ? (
            <span className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border-[3px] border-line bg-paper-warm px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-ink-muted sm:w-auto">Locked — Basic required</span>
          ) : exhausted ? (
            <span className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border-[3px] border-line bg-paper-warm px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-ink-muted sm:w-auto">Daily limit reached</span>
          ) : (
            <button type="button" onClick={() => onStart(effectiveTopic, selectedCard?.patternId)} disabled={!effectiveTopic} className="btn-3d inline-flex w-full items-center justify-center gap-2 rounded-2xl border-[3px] border-line bg-flag-red-fill px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-on-fill disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto" style={inkShadow}>
              Start with {TEACHER_NAME}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
            </button>
          )}
          {!isFreeLocked && !exhausted && (
            <button type="button" onClick={() => onStart("")} className="font-body text-[13px] font-bold text-ink-soft hover:text-ink">I just want to talk →</button>
          )}
        </div>
      </main>
    </div>
  );
}

function FocusCard({ focus, selected, onSelect, size }: { focus: FocusPattern; selected: boolean; onSelect: () => void; size: "large" | "normal" }) {
  return (
    <button type="button" aria-pressed={selected} onClick={onSelect} className={`block w-full rounded-2xl border-[3px] border-line text-left transition ${size === "large" ? "px-6 py-6" : "px-5 py-4"} ${selected ? "bg-ink-fill text-on-fill" : "bg-flag-gold-soft text-ink hover:bg-card hover:text-flag-red"}`} style={inkShadow}>
      <div className="flex items-start justify-between gap-3">
        <p className={`font-display font-black leading-tight ${size === "large" ? "text-[22px]" : "text-[16px]"}`}>{focus.label}</p>
        {focus.count7d > 0 && (
          <span className={`shrink-0 rounded-full border-2 px-2.5 py-0.5 font-body text-[11px] font-bold uppercase tracking-[0.1em] ${selected ? "border-card text-on-fill" : "border-line text-ink"}`}>{focus.count7d}× this week</span>
        )}
      </div>
      <p className={`mt-1.5 font-body leading-relaxed ${size === "large" ? "text-[15px]" : "text-[13px]"} ${selected ? "text-on-fill/80" : "text-ink-soft"}`}>{focus.description}</p>
    </button>
  );
}
