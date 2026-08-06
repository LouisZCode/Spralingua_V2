// GAME-001: synthesized feedback earcons. Design rules from the research pass:
// wins are short bright ascending major figures, fails are soft descending
// two-notes (never a buzzer), give-ups play nothing. Sounds are decorative —
// they must never throw, block, or fire when muted. AudioContext is created
// lazily and unlocked on the first real user gesture (browser autoplay policy);
// the mute preference is app-wide via localStorage so every surface honors it.

export type SoundKind = "win" | "bigwin" | "bonus" | "fail" | "bigfail" | "tick";

const MUTE_KEY = "spralingua-sound-muted-v1";

export function isMuted(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setMuted(muted: boolean): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(MUTE_KEY, muted ? "1" : "0");
  } catch {}
}

let ctx: AudioContext | null = null;

function getContext(): AudioContext | null {
  if (ctx) return ctx;
  if (typeof window === "undefined") return null;
  const Ctor =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext;
  if (!Ctor) return null;
  ctx = new Ctor();
  return ctx;
}

// Autoplay policy: a context created before any gesture starts "suspended"
// and never makes sound. These one-time listeners create/resume it on the
// first real interaction, then remove themselves — playSound also resumes
// defensively in case a context already existed but is still suspended.
if (typeof window !== "undefined") {
  const unlock = () => {
    const c = getContext();
    if (c && c.state === "suspended") {
      c.resume().catch(() => {});
    }
    window.removeEventListener("pointerdown", unlock);
    window.removeEventListener("keydown", unlock);
  };
  window.addEventListener("pointerdown", unlock);
  window.addEventListener("keydown", unlock);
}

// One oscillator + its own gain envelope: linear ramp up to `peak` over
// ~10ms (avoids a click), exponential ramp down to near-silence by the
// note's end (natural decay, never an abrupt cutoff).
function note(
  c: AudioContext,
  type: OscillatorType,
  freq: number,
  startAt: number,
  durationSec: number,
  peak: number,
  attackSec = 0.01
) {
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  osc.connect(gain);
  gain.connect(c.destination);
  const t0 = c.currentTime + startAt;
  const t1 = t0 + durationSec;
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.linearRampToValueAtTime(peak, t0 + attackSec);
  gain.gain.exponentialRampToValueAtTime(0.0001, t1);
  osc.start(t0);
  osc.stop(t1 + 0.02);
}

export function playSound(kind: SoundKind): void {
  if (isMuted()) return;
  const c = getContext();
  if (!c) return;
  try {
    if (c.state === "suspended") {
      c.resume().catch(() => {});
    }
    switch (kind) {
      case "tick":
        note(c, "sine", 880, 0, 0.05, 0.08);
        break;
      case "win":
        note(c, "triangle", 659.25, 0, 0.09, 0.12);
        note(c, "triangle", 880, 0.07, 0.14, 0.12);
        break;
      case "bigwin":
        note(c, "triangle", 523.25, 0, 0.2, 0.12);
        note(c, "triangle", 659.25, 0.09, 0.2, 0.12);
        note(c, "triangle", 783.99, 0.18, 0.2, 0.12);
        note(c, "triangle", 1046.5, 0.27, 0.2, 0.12);
        // Shimmer — an octave above the last note, quiet.
        note(c, "sine", 2093, 0.27, 0.3, 0.04);
        break;
      case "bonus":
        note(c, "sine", 783.99, 0, 0.16, 0.1);
        note(c, "sine", 987.77, 0.07, 0.16, 0.1);
        note(c, "sine", 1318.5, 0.14, 0.16, 0.1);
        break;
      case "fail":
        // Descending, warm, informational — never harsh. Rounded attack.
        note(c, "sine", 587.33, 0, 0.12, 0.09, 0.02);
        note(c, "sine", 440, 0.1, 0.18, 0.09, 0.02);
        break;
      case "bigfail":
        // "fail" scaled to summary weight: the same warm sine descent, one
        // note longer and slower so it reads as a verdict on the round rather
        // than on an item. Deliberately not sad — a weak round is information,
        // and the learner is about to be offered "Keep going".
        note(c, "sine", 587.33, 0, 0.2, 0.1, 0.02);
        note(c, "sine", 493.88, 0.14, 0.22, 0.1, 0.02);
        note(c, "sine", 392, 0.3, 0.34, 0.1, 0.02);
        break;
    }
  } catch {
    // Decorative — a synthesis failure must never surface to the learner.
  }
}
