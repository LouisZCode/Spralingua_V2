// UI-014: the one place load-failure copy lives. Every surface that fails a
// fetch renders loadError(<what it was loading>) — tone changes happen here.
export function loadError(what: string): string {
  return `Couldn't load ${what} — try again in a moment.`;
}
