"use client";

import { useEffect, useState } from "react";
import type { Level } from "../auth/AuthContext";
import {
  addPack,
  fetchPacks,
  UnauthorizedError,
  type PackSummary,
} from "./api";
import { loadError } from "../shared/copy";

// ONBOARD-002: which buckets each pack serves — the frontend mirror of the
// pack YAMLs' single `level:` plus the situation packs that legitimately
// serve two levels (same map as speedtest/pack_preview_gen.py::SERVES,
// home level first). Pack ids are stable slugs; when a pack is added or
// renamed, update this map alongside satz/packs/.
const SERVING_LEVELS: Record<string, string[]> = {
  "a1-basics": ["A1"],
  "first-days": ["A1"],
  "coffee-shop": ["A1", "A2"],
  "a2-basics": ["A2"],
  "doctor-visit": ["A2"],
  "at-work": ["A2", "B1"],
  "b1-everyday": ["B1"],
  "going-out": ["B1"],
  "job-interview": ["B1", "B2"],
  "b2-basics": ["B2"],
  bureaucracy: ["B2"],
};

// B2+ (auth bucket) ↔ B2 (pack YAML) — same normalization LEVEL-002 chose
// for the grammar picker.
function bucket(level: Level): string {
  return level === "B2+" ? "B2" : level;
}

const LEVEL_OPTIONS: { value: Level; hint: string }[] = [
  { value: "A1", hint: "just starting" },
  { value: "A2", hint: "knows the basics" },
  { value: "B1", hint: "gets by" },
  { value: "B2+", hint: "confident" },
];

// Which pack homes a bucket prefers, in recommendation order (home-level
// packs first, cross-served situation packs last).
function servesPack(packId: string, lvl: Level): boolean {
  return (SERVING_LEVELS[packId] ?? []).includes(bucket(lvl));
}

const redShadow = {
  ["--shadow-color"]: "var(--color-red-line)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

// First-run state (ONBOARD-002): an empty pool renders this inline chooser
// instead of the old "+ Add Cards" dead-end. One decision at a time — the
// learner's level picks the recommended packs, one tap lands a pack in the
// pool, and the parent's deck refetch flips the page straight into the
// round picker (SATZ-028 keeps the picker parked until the pool exists).
// Deliberately no "browse all" escape hatch — the funnel is level → one
// pack → picker; the full gallery (forge + all packs) lives behind the
// trainer's "+ Add Cards" once the pool exists.
export default function FirstRunChooser({
  token,
  level,
  onLevelChosen,
  onPoolChanged,
  onUnauthorized,
}: {
  token: string;
  level: Level | null;
  // AuthContext.setLevel — persists the pick (PUT /auth/level).
  onLevelChosen: (level: Level) => Promise<void>;
  onPoolChanged: () => void;
  onUnauthorized: () => void;
}) {
  const [packs, setPacks] = useState<PackSummary[] | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState<string | null>(null); // pack id being added
  // Local mirror of the level so the filter applies the instant a bucket is
  // tapped, without waiting for the AuthContext round trip.
  const [pickedLevel, setPickedLevel] = useState<Level | null>(level);

  useEffect(() => {
    let cancelled = false;
    fetchPacks(token)
      .then((p) => {
        if (!cancelled) setPacks(p);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof UnauthorizedError) {
          onUnauthorized();
        } else {
          setError(true);
        }
      });
    return () => {
      cancelled = true;
    };
    // onUnauthorized is a stable expireSession ref — token is the real trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function handleChooseLevel(value: Level) {
    setPickedLevel(value);
    try {
      await onLevelChosen(value);
    } catch {
      // Level persistence is best-effort — the recommendation still works
      // off the local mirror; the /practice picker asks again next session.
    }
  }

  async function handleAdd(pack: PackSummary) {
    setBusy(pack.id);
    try {
      await addPack(token, pack.id);
      onPoolChanged(); // deck refetch → parent swaps this chooser for the picker
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        onUnauthorized();
      } else {
        setError(true);
      }
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return (
      <div className="text-center">
        <h2 className="font-display text-[clamp(26px,5vw,36px)] font-black leading-tight tracking-tight text-ink">
          Your pool is empty.
        </h2>
        <p className="mt-3 font-body text-[14px] font-semibold text-flag-red-deep">
          {loadError("your packs")}
        </p>
      </div>
    );
  }

  const effectiveLevel = pickedLevel;
  const order = SERVING_LEVELS[effectiveLevel ?? ""] ?? [];
  const homeRank = (pack: PackSummary): number => {
    const home = SERVING_LEVELS[pack.id]?.[0];
    const rank = home ? order.indexOf(home) : -1;
    return rank === -1 ? 99 : rank; // cross-served packs sort after homes
  };
  const recommended = packs
    ? packs
        .filter((p) => servesPack(p.id, effectiveLevel as Level))
        .sort((a, b) => homeRank(a) - homeRank(b))
        .slice(0, 3)
    : [];

  return (
    <div className="text-center">
      <h2 className="font-display text-[clamp(26px,5vw,36px)] font-black leading-tight tracking-tight text-ink">
        Build your starter pool.
      </h2>

      {/* Level question — backstop only: /practice already asks the level
          once (LEVEL-001, LevelPickerModal) before any learner reaches this
          page. This inline card covers a direct deep link or an account
          that somehow skipped /practice. */}
      {effectiveLevel === null ? (
        <div className="mx-auto mt-8 max-w-[520px] rounded-[24px] border-[3px] border-line bg-card p-6 shadow-[0_5px_0_var(--color-line)]">
          <p className="font-body text-[11px] font-black uppercase tracking-[0.26em] text-ink-muted">
            How&apos;s your German?
          </p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            {LEVEL_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => handleChooseLevel(opt.value)}
                className="btn-3d rounded-[16px] border-[3px] border-line bg-elevated px-4 py-3 text-left transition-colors hover:bg-ink-fill hover:text-on-fill"
                style={inkShadow}
              >
                <span className="block font-display text-[15px] font-black uppercase tracking-[0.12em]">
                  {opt.value}
                </span>
                <span className="block font-body text-[11px] font-bold uppercase tracking-[0.14em] opacity-70">
                  {opt.hint}
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="mx-auto mt-8 max-w-[560px]">
          {!packs ? (
            <p className="font-body text-[12px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
              Loading packs…
            </p>
          ) : recommended.length === 0 ? (
            <p className="font-body text-[14px] font-semibold text-ink-soft">
              Nothing picked for {effectiveLevel} yet — check back soon.
            </p>
          ) : (
            <div className="flex flex-col gap-4">
              {recommended.map((pack) => (
                <div
                  key={pack.id}
                  className="flex items-center justify-between gap-4 rounded-[20px] border-[3px] border-line bg-card p-4 text-left shadow-[0_5px_0_var(--color-line)]"
                >
                  <div className="min-w-0">
                    <h3 className="font-display text-[18px] font-black leading-tight tracking-tight text-ink">
                      {pack.title}
                    </h3>
                    {pack.description && (
                      <p className="mt-0.5 font-body text-[13px] leading-snug text-ink-soft">
                        {pack.description}
                      </p>
                    )}
                    <p className="mt-1 font-body text-[11px] font-bold uppercase tracking-[0.16em] text-ink-muted">
                      {pack.cardCount} words
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleAdd(pack)}
                    disabled={busy !== null}
                    className="btn-3d shrink-0 rounded-[16px] border-[3px] border-red-line bg-flag-red-fill px-5 py-2.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-on-fill disabled:pointer-events-none disabled:opacity-40"
                    style={redShadow}
                  >
                    {busy === pack.id ? "Adding…" : "Add pack"}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
