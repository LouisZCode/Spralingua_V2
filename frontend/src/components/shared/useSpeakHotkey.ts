"use client";

import { useEffect, useRef } from "react";

// UI-012: hands-free Space/Enter control for the speaking trainers, so a full
// practice loop (record -> verdict -> next) never forces a reach for the
// mouse. One shared hook because VocabTrainer, SprechenTrainer and
// SzenarioTrainer all need identical keyboard guards; each trainer keeps
// deciding what the key actually DOES (start recording, stop, advance, or
// nothing) via `onTrigger` — this hook only decides whether a keypress
// should reach that callback at all.
//
// Guard order (each one short-circuits the next):
//   1. Autorepeat (a held key) never retriggers.
//   2. Typing in an input/textarea/select/contenteditable, or inside
//      anything that reads as a dialog/modal (PackModal, AddWordForm) —
//      leave the key alone, it's not ours to intercept.
//   3. A focused button or link already owns Enter/Space natively (the
//      Record button itself once clicked, "Try again", a modal's own close
//      button…) — skip and let that native activation fire instead of
//      double-firing. This is deliberately the simplest rule that can never
//      fight the browser: we only ever act when nothing focusable owns the
//      key already.
//   4. Only then: Space gets preventDefault (it scrolls the page otherwise)
//      and the trainer's callback runs.
export function useSpeakHotkey(onTrigger: () => void, enabled: boolean = true) {
  // Latest-ref idiom (same as recorder.ts's onStopRef): the window listener
  // binds once per `enabled` toggle and must always see the current closure
  // without churning the effect's dependency array on every render.
  const onTriggerRef = useRef(onTrigger);
  // eslint-disable-next-line react-hooks/refs -- latest-ref idiom, see above
  onTriggerRef.current = onTrigger;

  useEffect(() => {
    if (!enabled) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== " " && e.key !== "Enter") return;
      if (e.repeat) return;

      const target = e.target as HTMLElement | null;
      // Typing somewhere, or inside anything dialog-shaped — never ours.
      if (
        target?.closest(
          'input, textarea, select, [contenteditable="true"], [role="dialog"], [aria-modal="true"]'
        )
      ) {
        return;
      }
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      // Whatever currently has focus already handles Enter/Space itself —
      // stepping in too would double-fire it (the Record button, a
      // secondary "Try again", a modal's own control, all covered by the
      // same rule). Skip and let native activation win.
      const active = document.activeElement;
      if (active instanceof HTMLElement && active.closest("button, a")) {
        return;
      }

      if (e.key === " ") e.preventDefault();
      onTriggerRef.current();
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [enabled]);
}
