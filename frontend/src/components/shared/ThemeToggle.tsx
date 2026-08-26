"use client";

// DARK-001: the light/dark switch. Shaped exactly like SoundToggle so the two
// sit together in a header without looking like different controls.

import { useEffect, useState } from "react";
import { activeTheme, applyTheme, type Theme } from "./theme";

const inkShadow = {
  ["--shadow-color"]: "var(--color-line)",
} as React.CSSProperties;

export default function ThemeToggle() {
  // SSR-safe: the real value lives on <html data-theme>, stamped by the
  // pre-paint script in layout.tsx, so it can't be read during render.
  // Same hydrate-in-an-effect idiom as SoundToggle.
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    // Same one-frame hydration as SoundToggle: the truth lives on the DOM
    // attribute, which isn't readable during render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(activeTheme());
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  }

  const dark = theme === "dark";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={dark}
      title={dark ? "Switch to light mode" : "Switch to dark mode"}
      className="btn-3d inline-flex h-10 w-10 items-center justify-center rounded-2xl border-[3px] border-line bg-card text-ink"
      style={inkShadow}
    >
      {dark ? (
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4.5 w-4.5"
        >
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4.5 w-4.5"
        >
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
        </svg>
      )}
    </button>
  );
}
