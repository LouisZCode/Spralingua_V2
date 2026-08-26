// DARK-001: light/dark preference. Additive — light stays the default and
// the shipped light theme is untouched; dark is a new option a learner opts
// into. Persistence mirrors shared/sound.ts: one versioned localStorage key,
// every access try/caught (Safari private mode throws on localStorage), and
// never throws into a render.
//
// The <html data-theme> attribute is the single source of truth at runtime —
// globals.css keys its whole dark token set off it, and layout.tsx stamps it
// before first paint so there is no flash of the wrong theme.

export type Theme = "light" | "dark";

const THEME_KEY = "spralingua-theme-v1";

/** The learner's explicit choice, or null when they've never picked one. */
export function storedTheme(): Theme | null {
  if (typeof window === "undefined") return null;
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "dark" || v === "light" ? v : null;
  } catch {
    return null;
  }
}

/** What the OS asks for. Used only when there's no explicit choice. */
export function systemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

/** What's actually on screen right now — read from the stamped attribute. */
export function activeTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? "dark"
    : "light";
}

/** Apply and remember. Writing the attribute re-themes the app instantly:
 *  every colour utility compiles to var(--color-*), so nothing re-renders. */
export function applyTheme(theme: Theme): void {
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-theme", theme);
  }
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {}
}
