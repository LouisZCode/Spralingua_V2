"use client";

// UI-007: hover (desktop) / tap (mobile) any German word → a small popover
// with translation + example + "add to my words". Dependency-free — plain
// spans, pointer events, and a module-level session cache. Renders pixel-
// identical to plain text at rest (no permanent underline/color change);
// the only always-on style is `cursor-help`, which is visually inert until
// the pointer is actually over the word. The popover itself portals to
// `document.body` so its `position: fixed` always anchors to the viewport,
// even when a host ancestor (e.g. a `rise-in` animated container, which
// leaves behind a permanent `transform` via `animation-fill-mode: both`)
// would otherwise become its containing block.
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";
import type { GlossInfo } from "../satzschmiede/api";

// SSR renders "use client" components too; useLayoutEffect warns when it
// fires server-side. Nothing here needs to run before first paint on the
// server (the popover is always closed on first render), so fall back to
// useEffect there to keep the console clean.
const useIsomorphicLayoutEffect =
  typeof window !== "undefined" ? useLayoutEffect : useEffect;

// Session-level cache: re-hovering the same word never refetches. Keyed by
// the lowercased SURFACE word the learner interacted with (not the lemma —
// "Mantels" and "Mantel" cache separately, matching what was actually shown).
const glossCache = new Map<string, GlossInfo>();

const WORD_TOKEN = /[A-Za-zäöüßÄÖÜ]+(?:-[A-Za-zäöüßÄÖÜ]+)*/g;

type Token = { text: string; isWord: boolean };

// Splits into word tokens (letters + internal hyphens) and separator tokens
// (everything else — spaces, punctuation, digits), rendered as-is. Every
// character of the input is reproduced exactly; nothing is trimmed.
function tokenize(text: string): Token[] {
  const tokens: Token[] = [];
  let lastIndex = 0;
  WORD_TOKEN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = WORD_TOKEN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ text: text.slice(lastIndex, match.index), isWord: false });
    }
    tokens.push({ text: match[0], isWord: true });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    tokens.push({ text: text.slice(lastIndex), isWord: false });
  }
  return tokens;
}

type AddState = "idle" | "pending" | "done";

// SATZ-013: a successful add through the gloss path reports back how many
// gloss-adds remain today (0..3) so the popover can update its own counter
// without a second round trip. `void` covers callers with nothing to report
// (there are none left in this codebase, but the type stays permissive).
type AddResult = { glossRemaining?: number } | void;

type GlossableProps = {
  text: string;
  onGloss: (word: string, context: string) => Promise<GlossInfo>;
  onAdd?: (lemma: string) => Promise<AddResult>;
  className?: string;
  // UI-009: a word to skip glossing (case-insensitive, exact token match) —
  // e.g. the vocab card's own target, which the learner is meant to recall,
  // not look up. Absent means every word is glossable, as before.
  exclude?: string;
};

// `key={text}` forces a full remount whenever the underlying text changes
// (e.g. the next round's item) — React's own state-reset-on-prop-change
// idiom, so GlossableInner never has to manually reset itself mid-lifetime.
export default function Glossable(props: GlossableProps) {
  return <GlossableInner key={props.text} {...props} />;
}

function GlossableInner({ text, onGloss, onAdd, className, exclude }: GlossableProps) {
  const tokens = useMemo(() => tokenize(text), [text]);
  // UI-009: lowercased once — compared per-token below instead of allocating
  // a new lowercase string per render.
  const excludeLower = exclude ? exclude.toLowerCase() : null;

  // Which word token (by index into `tokens`) currently owns the popover —
  // null = closed. Only one popover open per Glossable instance.
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [glossData, setGlossData] = useState<GlossInfo | null>(null);
  const [glossLoading, setGlossLoading] = useState(false);
  const [addState, setAddState] = useState<AddState>("idle");
  const [addError, setAddError] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(
    null
  );

  const wordRefs = useRef<Record<number, HTMLSpanElement | null>>({});
  const popoverRef = useRef<HTMLSpanElement | null>(null);
  const openTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Guards a hover-triggered fetch: if the learner moves to another word (or
  // closes) before it resolves, a stale response must not clobber state.
  const requestIdRef = useRef(0);
  // The cache key (lowercased surface word) for whichever popover is open —
  // needed so a successful add can flip the right cache entry's inDeck.
  const activeWordKeyRef = useRef<string | null>(null);

  const closePopover = useCallback(() => {
    requestIdRef.current++;
    if (openTimerRef.current) {
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    activeWordKeyRef.current = null;
    setOpenIndex(null);
    setGlossData(null);
    setGlossLoading(false);
    setAddState("idle");
    setAddError(false);
  }, []);

  // Unmount cleanup — GlossableInner remounts on every text change (via the
  // `key` in Glossable above), so this only ever fires once per instance.
  useEffect(() => {
    return () => {
      if (openTimerRef.current) clearTimeout(openTimerRef.current);
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    };
  }, []);

  async function doOpen(index: number, word: string) {
    if (openTimerRef.current) {
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
    setOpenIndex(index);
    setAddState("idle");
    setAddError(false);

    const key = word.toLowerCase();
    activeWordKeyRef.current = key;
    const cached = glossCache.get(key);
    if (cached) {
      // SATZ-013: `cached.glossAddsRemaining` (if set) is whatever the
      // count was the moment this word was first glossed this session — a
      // later add elsewhere can move the real count without this entry
      // refreshing. Acceptable: it's a soft "about how many are left"
      // hint, not the enforcement (the backend re-checks on every add).
      setGlossData(cached);
      setGlossLoading(false);
      return;
    }

    setGlossData(null);
    setGlossLoading(true);
    const myRequestId = ++requestIdRef.current;
    try {
      const info = await onGloss(word, text);
      if (requestIdRef.current !== myRequestId) return; // superseded
      glossCache.set(key, info);
      setGlossData(info);
      setGlossLoading(false);
    } catch {
      if (requestIdRef.current !== myRequestId) return;
      // Never block reading with an error card — just close.
      closePopover();
    }
  }

  function cancelClose() {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }

  function scheduleClose() {
    cancelClose();
    closeTimerRef.current = setTimeout(() => {
      closeTimerRef.current = null;
      closePopover();
    }, 200);
  }

  function scheduleOpen(index: number, word: string) {
    cancelClose();
    if (openTimerRef.current) clearTimeout(openTimerRef.current);
    openTimerRef.current = setTimeout(() => {
      openTimerRef.current = null;
      void doOpen(index, word);
    }, 350);
  }

  function handleWordPointerEnter(
    e: ReactPointerEvent<HTMLSpanElement>,
    index: number,
    word: string
  ) {
    if (e.pointerType !== "mouse") return;
    scheduleOpen(index, word);
  }

  function handleWordPointerLeave(e: ReactPointerEvent<HTMLSpanElement>) {
    if (e.pointerType !== "mouse") return;
    if (openTimerRef.current) {
      // Left before the open-delay fired — never opened, nothing to close.
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
      return;
    }
    scheduleClose();
  }

  function handleWordPointerUp(
    e: ReactPointerEvent<HTMLSpanElement>,
    index: number,
    word: string
  ) {
    if (e.pointerType !== "touch") return;
    if (openIndex === index) {
      closePopover();
    } else {
      if (openTimerRef.current) {
        clearTimeout(openTimerRef.current);
        openTimerRef.current = null;
      }
      void doOpen(index, word);
    }
  }

  function handlePopoverPointerEnter(e: ReactPointerEvent<HTMLSpanElement>) {
    if (e.pointerType !== "mouse") return;
    cancelClose();
  }

  function handlePopoverPointerLeave(e: ReactPointerEvent<HTMLSpanElement>) {
    if (e.pointerType !== "mouse") return;
    scheduleClose();
  }

  async function handleAdd() {
    if (!onAdd || !glossData) return;
    setAddError(false);
    setAddState("pending");
    try {
      const result = await onAdd(glossData.lemma);
      // SATZ-013: fold the post-add remaining count back in when the caller
      // reports one; older callers returning void leave glossAddsRemaining
      // as it was (absent field stays absent, rendering unchanged below).
      const updated: GlossInfo = {
        ...glossData,
        inDeck: true,
        ...(result && typeof result.glossRemaining === "number"
          ? { glossAddsRemaining: result.glossRemaining }
          : {}),
      };
      setGlossData(updated);
      setAddState("done");
      if (activeWordKeyRef.current) {
        glossCache.set(activeWordKeyRef.current, updated);
      }
    } catch {
      setAddState("idle");
      setAddError(true);
    }
  }

  // Tap/click anywhere outside the open word and its popover closes it.
  useEffect(() => {
    if (openIndex === null) return;
    function onDocPointerDown(e: PointerEvent) {
      const target = e.target as Node;
      if (popoverRef.current?.contains(target)) return;
      if (wordRefs.current[openIndex as number]?.contains(target)) return;
      closePopover();
    }
    document.addEventListener("pointerdown", onDocPointerDown);
    return () => document.removeEventListener("pointerdown", onDocPointerDown);
  }, [openIndex, closePopover]);

  // A scroll/resize would leave the popover anchored to a stale position —
  // simplest correct behavior is to close it rather than track it live.
  useEffect(() => {
    if (openIndex === null) return;
    function onScrollOrResize() {
      closePopover();
    }
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);
    return () => {
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
    };
  }, [openIndex, closePopover]);

  // Position the popover above the word (below if there's no room above),
  // clamped horizontally inside the viewport. Recomputed whenever the
  // popover's content changes size (loading → loaded → add-footer states).
  useIsomorphicLayoutEffect(() => {
    if (openIndex === null) {
      setCoords(null);
      return;
    }
    const anchor = wordRefs.current[openIndex];
    const pop = popoverRef.current;
    if (!anchor || !pop) return;
    const anchorRect = anchor.getBoundingClientRect();
    const popRect = pop.getBoundingClientRect();
    const margin = 8;

    let top = anchorRect.top - popRect.height - margin;
    if (top < margin) {
      top = anchorRect.bottom + margin;
    }

    let left = anchorRect.left + anchorRect.width / 2 - popRect.width / 2;
    const maxLeft = window.innerWidth - popRect.width - margin;
    left = Math.min(Math.max(left, margin), Math.max(margin, maxLeft));

    setCoords({ top, left });
  }, [openIndex, glossLoading, glossData, addState, addError]);

  return (
    <span className={className}>
      {tokens.map((tok, i) =>
        tok.isWord && tok.text.toLowerCase() !== excludeLower ? (
          <span
            key={i}
            ref={(el) => {
              wordRefs.current[i] = el;
            }}
            tabIndex={0}
            className="cursor-help outline-none decoration-ink-muted decoration-dotted decoration-[1.5px] underline-offset-[3px] hover:underline focus:underline"
            onPointerEnter={(e) => handleWordPointerEnter(e, i, tok.text)}
            onPointerLeave={handleWordPointerLeave}
            onPointerUp={(e) => handleWordPointerUp(e, i, tok.text)}
          >
            {tok.text}
          </span>
        ) : (
          <span key={i}>{tok.text}</span>
        )
      )}

      {openIndex !== null &&
        // Portaled to document.body (see top-of-file comment) so `position:
        // fixed` below anchors to the viewport regardless of host ancestors.
        // No `mounted` guard needed for SSR: this branch only renders once
        // `openIndex !== null`, and `openIndex` starts at `null`, so
        // createPortal can never execute during server render or hydration's
        // first pass.
        createPortal(
          <span
            ref={popoverRef}
            onPointerEnter={handlePopoverPointerEnter}
            onPointerLeave={handlePopoverPointerLeave}
            style={{
              position: "fixed",
              top: coords ? coords.top : -9999,
              left: coords ? coords.left : -9999,
              visibility: coords ? "visible" : "hidden",
            }}
            className="z-50 block w-[240px] max-w-[260px] rounded-[14px] border-[3px] border-ink bg-white px-3.5 py-3 text-left font-body normal-case not-italic tracking-normal text-ink shadow-[4px_4px_0_var(--color-ink)]"
          >
            {glossLoading ? (
              <span className="block text-[12px] text-ink-muted">…</span>
            ) : glossData ? (
              <>
                <span className="block font-display text-[14px] font-black leading-snug text-ink">
                  {glossData.article ? `${glossData.article} ` : ""}
                  {glossData.lemma}
                </span>
                <span className="mt-1 block text-[12px] leading-snug text-ink-soft">
                  {glossData.gloss}
                </span>
                {glossData.example && (
                  <span className="mt-1 block italic leading-snug text-[12px] text-ink-muted">
                    {glossData.example}
                  </span>
                )}
                {onAdd && glossData.glossable !== false && (
                  <span className="mt-2 block border-t-2 border-ink/10 pt-2">
                    {glossData.inDeck ? (
                      <span className="block text-[11px] font-semibold text-ink-muted">
                        In deinem Deck ✓
                      </span>
                    ) : addState === "done" ? (
                      <span className="block text-[11px] font-semibold text-success">
                        Hinzugefügt ✓
                      </span>
                    ) : glossData.glossAddsRemaining === 0 ? (
                      // SATZ-013: cap hit — the button never renders, so there
                      // is no way to fire an add this backend would reject.
                      <span className="block text-[11px] font-semibold text-ink-muted">
                        Tageslimit erreicht
                      </span>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={handleAdd}
                          disabled={addState === "pending"}
                          className="inline-flex items-center rounded-full border-2 border-flag-red-deep bg-flag-red px-3 py-1 text-[11px] font-black uppercase tracking-[0.08em] text-white disabled:opacity-50"
                        >
                          {addState === "pending" ? "…" : "＋ Zu meinen Wörtern"}
                        </button>
                        {typeof glossData.glossAddsRemaining === "number" && (
                          <span className="mt-1 block text-[10px] font-semibold text-ink-faint">
                            {`noch ${glossData.glossAddsRemaining} heute`}
                          </span>
                        )}
                      </>
                    )}
                    {addError && (
                      <span className="mt-1 block text-[11px] font-semibold text-flag-red-deep">
                        Konnte nicht hinzufügen
                      </span>
                    )}
                  </span>
                )}
              </>
            ) : null}
          </span>,
          document.body
        )}
    </span>
  );
}
