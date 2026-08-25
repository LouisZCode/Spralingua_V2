"use client";

import { useEffect, useState } from "react";
import { useCoinBalance, useCoinsBypassed } from "@/components/shared/Coins";
import Link from "next/link";

// PAY-002: LETTER = 15 coins per letter (full cycle with 2 attempts)
const LETTER_COST = 15;

const LETTER_PRESETS = [
  { value: 1, label: "1", costLabel: "≈ 15 coins" },
  { value: 2, label: "2", costLabel: "≈ 30 coins" },
  { value: 3, label: "3", costLabel: "≈ 45 coins" },
] as const;

const BRIEFKASTEN_STORAGE_KEY = "briefkasten-rounds-v1";

function readStoredChoice(): number {
  try {
    const raw = localStorage.getItem(BRIEFKASTEN_STORAGE_KEY);
    if (raw) {
      const n = parseInt(raw, 10);
      if (Number.isFinite(n) && n >= 1 && n <= 10) return n;
    }
  } catch {}
  return 1;
}

function persistChoice(choice: number) {
  try {
    localStorage.setItem(BRIEFKASTEN_STORAGE_KEY, String(choice));
  } catch {}
}

interface BriefkastenPickerProps {
  onStart: (count: number) => void;
}

export default function BriefkastenPicker({ onStart }: BriefkastenPickerProps) {
  const [presetChoice, setPresetChoice] = useState<number>(1);
  const [customText, setCustomText] = useState<string>("");
  const bal = useCoinBalance();
  const bypassed = useCoinsBypassed();

  // SSR-safe hydration from localStorage, same idiom as Flow.tsx's round
  // picker — reading storage during render would mismatch the server HTML.
  // The setState is the whole point of the effect, hence the disable: the
  // rule fires here but not on Flow's identical effect, which sits in a
  // component too large for the compiler's analysis to reach.
  useEffect(() => {
    const stored = readStoredChoice();
    if (LETTER_PRESETS.some((p) => p.value === stored)) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPresetChoice(stored);
    } else {
      setCustomText(String(stored));
    }
  }, []);

  const customNumber = customText === "" ? null : Number(customText);
  const roundChoice = customNumber ?? presetChoice;
  const cost = roundChoice * LETTER_COST;
  const maxAffordable = bal ? Math.floor(bal.balance / LETTER_COST) : 10;
  // PAY-002: developer bypass — same trap as SatzschmiedePicker (see there).
  const effectiveMax = bypassed ? 10 : Math.min(10, maxAffordable);
  const insufficient = !bypassed && bal !== null && bal.balance < cost;
  const disabled = insufficient || roundChoice < 1 || roundChoice > effectiveMax;

  const handlePickPreset = (value: number) => {
    setPresetChoice(value);
    setCustomText("");
  };

  const handleCustomChange = (raw: string) => {
    const digits = raw.replace(/[^0-9]/g, "");
    if (digits === "") {
      setCustomText("");
      return;
    }
    const n = Math.min(10, Math.max(1, Math.trunc(Number(digits))));
    if (n > effectiveMax) {
      setCustomText(String(effectiveMax));
    } else {
      setCustomText(String(n));
    }
  };

  const handleStart = () => {
    persistChoice(roundChoice);
    onStart(roundChoice);
  };

  return (
    <div className="rise-in">
      <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
        Briefkasten
      </p>
      <h1 className="mt-3 font-display text-[clamp(28px,4.6vw,44px)] font-black leading-[1.03] tracking-tight text-ink">
        How many letters to write?
      </h1>
      <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
        Each letter includes hints first, then corrections, then how a German would really say it. One letter costs 15 coins.
      </p>

      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        {LETTER_PRESETS.map(({ value, label, costLabel }) => {
          const selected = customText === "" && presetChoice === value;
          return (
            <button
              key={value}
              type="button"
              aria-pressed={selected}
              onClick={() => handlePickPreset(value)}
              className={`btn-3d rounded-3xl border-[3px] border-ink px-6 py-6 text-center transition ${
                selected ? "bg-ink text-white" : "bg-white text-ink hover:bg-paper-warm"
              }`}
            >
              <span className="font-display text-[28px] font-black">{label}</span>
              <span className="mt-1 block font-body text-[11px] font-bold uppercase tracking-[0.14em] opacity-70">
                {costLabel}
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-7 flex items-center gap-3">
        <label
          htmlFor="briefkasten-custom-rounds"
          className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted"
        >
          Or a custom number
        </label>
        <input
          id="briefkasten-custom-rounds"
          type="text"
          inputMode="numeric"
          value={customText}
          onChange={(e) => handleCustomChange(e.target.value)}
          placeholder={`1–${effectiveMax}`}
          className="w-24 rounded-2xl border-[3px] border-ink bg-white px-4 py-2 text-center font-body text-[16px] text-ink placeholder:text-ink-muted focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
          disabled={bypassed ? false : bal === null}
        />
      </div>

      {/* Balance / cost note */}
      {bal && !bypassed && (
        <p className="mt-3 font-body text-[12px] text-ink-muted">
          🪙 {bal.balance} coins · this round costs {cost} coins
          {insufficient && (
            <>
              {" — "}
              <Link href="/pricing" className="font-bold text-flag-red underline underline-offset-2">
                Get more coins
              </Link>{" "}
              or pick fewer letters.
            </>
          )}
        </p>
      )}
      {bal && bypassed && (
        <p className="mt-3 font-body text-[12px] text-ink-muted">
          🪙 developer · this round is free
        </p>
      )}

      <div className="mt-9">
        <button
          type="button"
          onClick={handleStart}
          disabled={disabled}
          className="btn-3d inline-flex items-center gap-2 rounded-2xl border-[3px] border-flag-red-deep bg-flag-red px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          Start
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
        </button>
        {insufficient && (
          <p className="mt-2 font-body text-[13px] font-semibold text-flag-red-deep">
            Not enough coins for this round —{" "}
            <Link href="/pricing" className="underline underline-offset-2">
              get more coins
            </Link>{" "}
            or pick fewer letters.
          </p>
        )}
      </div>
    </div>
  );
}
