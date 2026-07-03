"use client";

import { useEffect, useState } from "react";
import {
  addPack,
  fetchPacks,
  UnauthorizedError,
  type PackSummary,
} from "./api";

// The pack shop: browse curated word packs (level packs + situation packs)
// and add them to your pool. Adding is idempotent server-side, so overlapping
// packs (shared canonical cards) simply top up what's missing.
const KIND_CHIP: Record<PackSummary["kind"], string> = {
  level: "bg-flag-gold text-ink",
  situation: "bg-flag-red text-white",
};

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

export default function PackGallery({
  token,
  onPoolChanged,
  onPractice,
  onUnauthorized,
}: {
  token: string;
  // Fired after every successful add so the parent can refetch the deck.
  onPoolChanged: () => void;
  // Jump to the trainer; parent hides the button while the pool is empty.
  onPractice: (() => void) | null;
  // Session JWT expired mid-browse — parent signs out for a fresh sign-in.
  onUnauthorized: () => void;
}) {
  const [packs, setPacks] = useState<PackSummary[] | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState<string | null>(null); // pack id being added

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
    // onUnauthorized is a stable signOut ref — token is the real trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function handleAdd(pack: PackSummary) {
    setBusy(pack.id);
    try {
      await addPack(token, pack.id);
      // Reflect ownership locally (full pack now owned) without a refetch.
      setPacks(
        (prev) =>
          prev?.map((p) =>
            p.id === pack.id ? { ...p, ownedCount: p.cardCount } : p
          ) ?? null
      );
      onPoolChanged();
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
      <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
        Couldn&apos;t load the packs — is the backend running?
      </p>
    );
  }
  if (!packs) {
    return (
      <p className="text-center font-body text-[12px] font-semibold uppercase tracking-[0.26em] text-ink-muted">
        Loading packs…
      </p>
    );
  }

  return (
    <div>
      <div className="grid gap-5 sm:grid-cols-2">
        {packs.map((pack) => {
          const owned = pack.ownedCount >= pack.cardCount;
          const partial = !owned && pack.ownedCount > 0;
          return (
            <div
              key={pack.id}
              className="flex flex-col rounded-[24px] border-[3px] border-ink bg-white p-5 shadow-[0_5px_0_var(--color-ink)]"
            >
              <div className="flex items-start justify-between gap-3">
                <h3 className="font-display text-[20px] font-black leading-tight tracking-tight text-ink">
                  {pack.title}
                </h3>
                <span
                  className={`inline-flex shrink-0 items-center rounded-full border-[2px] border-ink px-2.5 py-0.5 font-body text-[10px] font-black uppercase tracking-[0.16em] ${KIND_CHIP[pack.kind]}`}
                >
                  {pack.level ?? pack.kind}
                </span>
              </div>
              {pack.description && (
                <p className="mt-1.5 font-body text-[13px] leading-snug text-ink-soft">
                  {pack.description}
                </p>
              )}
              <div className="mt-auto flex items-center justify-between gap-3 pt-4">
                <span className="font-body text-[11px] font-bold uppercase tracking-[0.16em] text-ink-muted">
                  {pack.cardCount} words
                  {partial && ` · ${pack.ownedCount} in your pool`}
                </span>
                {owned ? (
                  <span className="inline-flex items-center rounded-[16px] border-[3px] border-ink bg-success-soft px-4 py-1.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-success">
                    Added ✓
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => handleAdd(pack)}
                    disabled={busy !== null}
                    className="btn-3d inline-flex items-center rounded-[16px] border-[3px] border-flag-red-deep bg-flag-red px-4 py-1.5 font-display text-[12px] font-black uppercase tracking-[0.14em] text-white disabled:pointer-events-none disabled:opacity-40"
                    style={redShadow}
                  >
                    {busy === pack.id
                      ? "Adding…"
                      : partial
                        ? "Add the rest"
                        : "Add pack"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {onPractice && (
        <div className="mt-8 text-center">
          <button
            type="button"
            onClick={onPractice}
            className="btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] border-ink bg-white px-7 py-3 font-display text-[14px] font-black uppercase tracking-[0.16em] text-ink"
            style={inkShadow}
          >
            Practice your pool →
          </button>
        </div>
      )}
    </div>
  );
}
