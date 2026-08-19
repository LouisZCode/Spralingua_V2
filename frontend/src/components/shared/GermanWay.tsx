"use client";

// IDIOM-002 Proposal-1: the on-demand "How would a German say this?" button.
// One judge call per tap (POST /idiom/rephrase), zero cost otherwise. The
// null answer is first-class: when the learner's line already sounds German
// the reveal is a quiet confirmation, never an empty card. Phrasing advice
// only — this never touches the verdict rendered by the surface that mounts
// it (SATZ-008: style is not an error, and this must not become one through
// the back door).
//
// Two looks: `card` (default) matches BriefTrainer's natural_version toggle
// for the drill verdict cards; `inline` is a miniature of the same pill,
// right-aligned under the learner's own tandem bubbles.
//
// Two data flows: the default is on-demand (`ask` posts /idiom/rephrase the
// first time the button is pressed, every caller above used this). Briefkasten
// is different — its Germanize call already ran server-side alongside the
// attempt-2 judge, so there is nothing left to fetch. Passing `value` skips
// the network call entirely and renders that pre-fetched verdict instead;
// `autoExpand` opens the card on mount so the rewrite is visible without a
// click, while the button still offers a plain collapse/reopen toggle.

import { useEffect, useState } from "react";
import { HTTP_BASE } from "@/lib/api";
import { useAuth } from "../auth/AuthContext";
import { diffTokens, MarkedText, type MarkedToken } from "./feedback";
import Glossable from "./Glossable";
import type { GlossInfo } from "../satzschmiede/api";

// Just the rewrite — no explanation field by design (user call, 2026-08-10):
// the German version next to the learner's own line speaks for itself.
type Rephrase = { natural: string | null };

const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

export default function GermanWay({
  text,
  context,
  target,
  variant = "card",
  onGloss,
  onAdd,
  exclude,
  onRephrase,
  value,
  autoExpand,
}: {
  // The learner's own line — a verdict card's transcript or a chat bubble.
  text: string;
  // What the line answered (task prompt / partner's turn) — judge context
  // only, never graded.
  context?: string;
  // IDIOM-004 P1: the card/vocab word this line was supposed to practise,
  // when the host knows it (VocabTrainer does — the other hosts don't have
  // a single target word and simply omit this). Lets the judge discard a
  // rewrite that drops the very word the learner came here to use.
  target?: string;
  variant?: "card" | "inline";
  // UI-007/SATZ-018: same optional gloss wiring as VocabTrainer's corrected-
  // sentence diff — hover/tap a word in the Germanized rewrite for a
  // translation + "add to my words". Undefined when the parent doesn't wire
  // it, in which case MarkedText renders its original plain-text tokens.
  onGloss?: (word: string, context: string) => Promise<GlossInfo>;
  onAdd?: (lemma: string) => Promise<{ glossRemaining?: number } | void>;
  // UI-009: a word to skip glossing, forwarded to Glossable.
  exclude?: string;
  // Fires once the judge call lands with an ACTUAL rewrite — never on the
  // "already sounds German" (`natural: null`) case. Lets a host (e.g.
  // VocabTrainer's blue "Try again") offer a rehearsal keyed to a genuine
  // rephrase instead of every tap of the button.
  onRephrase?: () => void;
  // Briefkasten only: a verdict the caller already has (its Germanize call
  // ran server-side during attempt 2). `undefined` (the default, every other
  // caller) means "fetch on first click" as before; passing an object —
  // even `{ natural: null }` — switches this instance to render-only, no
  // POST ever fires.
  value?: Rephrase;
  // Open the card immediately when `value` lands, no click needed. Only
  // meaningful together with `value`; ignored on the fetch path.
  autoExpand?: boolean;
}) {
  const { token } = useAuth();
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">(
    "idle"
  );
  const [result, setResult] = useState<Rephrase | null>(null);
  const [open, setOpen] = useState(false);

  // A new attempt is a new line — drop the old judgement. When `value` is
  // supplied, adopt it straight into the "done" state instead: there is no
  // fetch to do, and skipping "idle" means the button never flashes its
  // pre-judged label.
  // Deps are the PRIMITIVES inside `value`, not the object itself: callers
  // pass an inline literal (new identity every render), and depending on the
  // object would re-run this effect on every parent re-render — undoing a
  // collapse the user just made. `hasValue`/`valueNatural` only change when
  // a genuinely new verdict lands.
  const hasValue = value !== undefined;
  const valueNatural = value?.natural ?? null;
  useEffect(() => {
    if (hasValue) {
      setState("done");
      setResult({ natural: valueNatural });
      setOpen(!!autoExpand);
      return;
    }
    setState("idle");
    setResult(null);
    setOpen(false);
  }, [text, hasValue, valueNatural, autoExpand]);

  if (!text.trim() || !token) return null;

  const ask = async () => {
    // Already judged (fetched or handed in via `value`): the button is a
    // plain visibility toggle from here on, never a fetch trigger.
    if (value !== undefined || state === "done") {
      setOpen((v) => !v);
      return;
    }
    setState("loading");
    try {
      const res = await fetch(`${HTTP_BASE}/idiom/rephrase`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ text, context: context ?? null, target: target ?? null }),
      });
      if (!res.ok) throw new Error(`rephrase failed (${res.status})`);
      const data = (await res.json()) as Rephrase;
      setResult(data);
      setState("done");
      setOpen(true);
      // Only a genuine rewrite counts — the "already sounds German" null
      // case has nothing to rehearse.
      if (data.natural) onRephrase?.();
    } catch {
      // Advice is optional — a failed call degrades to a retryable button,
      // never an error screen.
      setState("error");
    }
  };

  // Light the words the judge changed in BLUE, not red — the learner's line
  // wasn't wrong, a native just phrases it differently (user call,
  // 2026-08-10). Case-insensitive: the attempt is an STT transcript whose
  // casing is an ASR artifact (SATZ-016).
  const naturalDiff =
    state === "done" && result?.natural
      ? diffTokens(text, result.natural, { caseInsensitive: true }).corrected
      : null;

  // SATZ-018: same MarkedText per-token override VocabTrainer's corrected-
  // sentence diff uses (see shared/feedback.tsx) — each token keeps its blue
  // diff coloring but renders through Glossable instead of plain text. The
  // context sent to onGloss is always the FULL Germanized sentence, not the
  // single tapped token. `undefined` when the parent doesn't wire onGloss,
  // so MarkedText falls back to its original plain-text rendering.
  const renderToken = onGloss
    ? (t: MarkedToken) => (
        <Glossable
          text={t.text}
          onGloss={(word: string) => onGloss(word, result?.natural ?? "")}
          onAdd={onAdd}
          exclude={exclude}
        />
      )
    : undefined;

  const revealed =
    state === "done" &&
    open &&
    (result?.natural && naturalDiff ? (
      variant === "card" ? (
        <div className="mt-3 rounded-[18px] border-[3px] border-ink bg-paper-warm px-4 py-3 text-left">
          <p className="font-body text-[15px] leading-relaxed text-ink">
            <MarkedText tokens={naturalDiff} mark="blue" renderToken={renderToken} />
          </p>
        </div>
      ) : (
        <div className="mt-1.5 max-w-[420px] rounded-[14px] border-[2px] border-ink/40 bg-paper-warm px-3 py-2 text-left">
          <p className="font-body text-[13px] leading-snug text-ink">
            <MarkedText tokens={naturalDiff} mark="blue" renderToken={renderToken} />
          </p>
        </div>
      )
    ) : (
      <p
        className={
          variant === "card"
            ? "mt-2 font-body text-[12px] font-bold uppercase tracking-[0.16em] text-success"
            : "mt-1 font-body text-[10px] font-bold uppercase tracking-[0.14em] text-success"
        }
      >
        ✓ Already sounds German
      </p>
    ));

  if (variant === "inline") {
    return (
      <div className="mt-1 flex min-w-0 flex-col items-end">
        <button
          type="button"
          onClick={ask}
          disabled={state === "loading"}
          className="btn-3d inline-flex items-center rounded-full border-[2px] border-ink bg-white px-3 py-1 font-display text-[10px] font-black uppercase tracking-[0.14em] text-ink disabled:opacity-50"
          style={inkShadow}
        >
          {state === "loading"
            ? "Asking…"
            : state === "error"
              ? "Couldn't ask — retry?"
              : state === "done" && open
                ? "Hide"
                : "How would a German say this?"}
        </button>
        {revealed}
      </div>
    );
  }

  return (
    <div className="mt-5 text-center">
      <button
        type="button"
        onClick={ask}
        disabled={state === "loading"}
        className="btn-3d inline-flex items-center rounded-[18px] border-[3px] border-ink bg-white px-5 py-2 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink disabled:opacity-60"
        style={inkShadow}
      >
        {state === "loading"
          ? "Asking…"
          : state === "done" && open
            ? "Hide ▴"
            : "How would a German say this? ▾"}
      </button>
      {state === "error" && (
        <p className="mt-2 font-body text-[12px] font-semibold text-flag-red-deep">
          Couldn&apos;t reach the judge — tap to try again.
        </p>
      )}
      {revealed}
    </div>
  );
}
