"use client";

// The one celebration moment, shared (ONBOARD-004 update + PAY-009): the
// welcome-gift popup and the upgrade-success screen both call celebrate()
// so the two payoffs look and feel identical. Confetti only — NO audio
// (owner decision 2026-10-26: browsers block sound without a user gesture,
// and the welcome popup appears right after the Google redirect, so a chime
// would mostly not play; silence also fails silent by construction).
//
// House rules honoured here: colours are read from the CSS theme tokens at
// call time (never hardcoded — light and dark both stay on-brand), and the
// whole thing no-ops for prefers-reduced-motion users.
//
// Preview locally (dev server only): http://localhost:3000/celebrate-preview

import confetti from "canvas-confetti";

function palette(): string[] {
  const s = getComputedStyle(document.documentElement);
  const pick = (name: string) => s.getPropertyValue(name).trim();
  return [
    pick("--color-flag-red"),
    pick("--color-flag-gold"),
    pick("--color-gender-blue"),
    pick("--color-success"),
  ].filter(Boolean);
}

export function celebrate(kind: "soft" | "big" = "soft"): void {
  if (typeof window === "undefined") return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colors = palette();
  // Above every surface including modals (ConsentGate/gift live at z-[90]).
  const z = 1000;
  if (kind === "big") {
    // PAY-009 upgrade payoff: two side cannons sweeping inward + a center burst.
    confetti({ particleCount: 90, angle: 60, spread: 60, origin: { x: 0, y: 0.7 }, colors, zIndex: z });
    confetti({ particleCount: 90, angle: 120, spread: 60, origin: { x: 1, y: 0.7 }, colors, zIndex: z });
    confetti({ particleCount: 160, spread: 100, origin: { y: 0.6 }, colors, zIndex: z, scalar: 1.1 });
  } else {
    // ONBOARD-004 welcome gift: one soft burst from the middle.
    confetti({
      particleCount: 110,
      spread: 75,
      startVelocity: 38,
      origin: { y: 0.6 },
      colors,
      zIndex: z,
    });
  }
}
