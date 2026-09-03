"use client";

import { useEffect, useState } from "react";
import { useCoinBalance, useCoinsBypassed } from "@/components/shared/Coins";
import { SATZ_ATTEMPT_COST } from "@/lib/coins";

const SATZ_PRESETS = [
  { value: 10, label: "10" },
  { value: 20, label: "20" },
  { value: 30, label: "30" },
] as const;

const SATZ_STORAGE_KEY = "satzschmiede-rounds-v1";

function readStoredChoice(): number | null {
  try {
    const raw = localStorage.getItem(SATZ_STORAGE_KEY);
    if (raw) {
      const n = parseInt(raw, 10);
      if (Number.isFinite(n) && n >= 1 && n <= 50) return n;
    }
  } catch {}
  // PAY-005: no stored preference — the affordable default decides.
  return null;
}

function persistChoice(choice: number) {
  try {
    localStorage.setItem(SATZ_STORAGE_KEY, String(choice));
  } catch {}
}

function clampRounds(n: number): number {
  return Math.min(50, Math.max(1, Math.trunc(n)));
}

// PAY-005: the largest preset the balance can cover, or the smallest preset
// when nothing is affordable — the Start button stays the gate; the default
// just never opens on a round the account can't buy.
function largestAffordablePreset(max: number): number {
  const affordable = SATZ_PRESETS.filter((p) => p.value <= max);
  return affordable.length > 0
    ? affordable[affordable.length - 1].value
    : SATZ_PRESETS[0].value;
}

interface SatzschmiedePickerProps {
  onStart: (count: number) => void;
}

export default function SatzschmiedePicker({ onStart }: SatzschmiedePickerProps) {
  // PAY-005: the smallest preset renders until the balance resolves — the
  // affordable seed can then only ever move the default UP, never 30→10.
  const [presetChoice, setPresetChoice] = useState<number>(SATZ_PRESETS[0].value);
  const [customText, setCustomText] = useState<string>("");
  const bal = useCoinBalance();
  const bypassed = useCoinsBypassed();
  const maxAffordable = bal ? Math.floor(bal.balance / SATZ_ATTEMPT_COST) : 50;
  // PAY-002: developer bypass — never cap affordable to 0 for devs (balance is
  // frozen at 100 by design; clamping to 0 would lock Start for any 10/20/30).
  const effectiveMax = bypassed ? 50 : Math.min(50, maxAffordable);

  // PAY-005: seed once the balance resolves. The stored choice wins only
  // while it is still affordable; otherwise the default is the largest
  // preset the account can actually buy. Developers seed immediately at the
  // largest preset via the bypass. Replaces the mount-only localStorage
  // hydration — same SSR-safe idiom, the setState is the whole point of the
  // effect, hence the disables.
  const [affordableSeeded, setAffordableSeeded] = useState(false);
  useEffect(() => {
    if (affordableSeeded) return;
    if (bal === null && !bypassed) return; // balance not resolved yet
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAffordableSeeded(true);
    const stored = readStoredChoice();
    const seed =
      stored !== null && stored <= effectiveMax
        ? stored
        : largestAffordablePreset(effectiveMax);
    if (SATZ_PRESETS.some((p) => p.value === seed)) {
       
      setPresetChoice(seed);
    } else {
       
      setCustomText(String(seed));
    }
  }, [affordableSeeded, bal, bypassed, effectiveMax]);

  const customNumber = customText === "" ? null : Number(customText);
  const roundChoice = customNumber ?? presetChoice;
  const cost = roundChoice * SATZ_ATTEMPT_COST;
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
    const n = clampRounds(Number(digits));
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
        Satzschmiede
      </p>
      <h1 className="mt-3 font-display text-[clamp(28px,4.6vw,44px)] font-black leading-[1.03] tracking-tight text-ink">
        How many words to practice?
      </h1>
      <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
        Pick a round length — each word costs 5 coins.
      </p>

      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        {SATZ_PRESETS.map(({ value, label }) => {
          const selected = customText === "" && presetChoice === value;
          return (
            <button
              key={value}
              type="button"
              aria-pressed={selected}
              onClick={() => handlePickPreset(value)}
              className={`btn-3d rounded-3xl border-[3px] border-line px-6 py-6 text-center transition ${
                selected ? "bg-ink-fill text-on-fill" : "bg-card text-ink hover:bg-paper-warm"
              }`}
            >
              <span className="font-display text-[28px] font-black">{label}</span>
              <span className="mt-1 block font-body text-[11px] font-bold uppercase tracking-[0.14em] opacity-70">
                ≈ {value * SATZ_ATTEMPT_COST} coins
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-7 flex items-center gap-3">
        <label
          htmlFor="satzschmiede-custom-rounds"
          className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted"
        >
          Or a custom number
        </label>
        <input
          id="satzschmiede-custom-rounds"
          type="text"
          inputMode="numeric"
          value={customText}
          onChange={(e) => handleCustomChange(e.target.value)}
          placeholder={`1–${effectiveMax}`}
          className="w-24 rounded-2xl border-[3px] border-line bg-card px-4 py-2 text-center font-body text-[16px] text-ink placeholder:text-ink-muted focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
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
              <a href="/pricing" className="font-bold text-flag-red underline underline-offset-2">
                Get more coins
              </a>{" "}
              or pick a smaller round.
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
          className="btn-3d inline-flex items-center gap-2 rounded-2xl border-[3px] border-red-line bg-flag-red-fill px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-on-fill disabled:cursor-not-allowed disabled:opacity-40"
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
            <a href="/pricing" className="underline underline-offset-2">
              get more coins
            </a>{" "}
            or pick a smaller round.
          </p>
        )}
      </div>
    </div>
  );
}
