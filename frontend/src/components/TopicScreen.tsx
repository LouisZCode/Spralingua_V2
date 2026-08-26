"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { HTTP_BASE } from "@/lib/api";
import { useCoinBalance, useCoinsBypassed } from "./shared/Coins";
import type { TandemPartner } from "./shared/tandem";

// The Grammatik-Tandem pre-conversation screen (TANDEM-001, Phase 3 MVP;
// TAND-005 grew the topic pool and reworked the suggestions below; TAND-008
// parameterized it by partner). The learner picks what to talk about with
// their chosen partner: one of 3 randomly recommended topics from that
// partner's own pool (tap a card to select it, or shuffle for a fresh 3), or
// their own free-text theme — either way, the Start button is what actually
// begins the chat. The chosen string becomes the `topic` param handed to
// ConversationView.
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// TAND-003: Natural (streaming VAD, mic always hot) vs Practice (tap record
// -> speak at your own pace -> tap stop -> auto-send) input mode, persisted
// like Szenario's B1/B2 tier toggle (Szenario.tsx). Default is Natural — the
// existing behavior stays byte-identical when selected.
const MODE_STORAGE_KEY = "tandem-input-mode-v1";

type InputMode = "natural" | "practice";

function readMode(): InputMode {
  try {
    return localStorage.getItem(MODE_STORAGE_KEY) === "practice"
      ? "practice"
      : "natural";
  } catch {
    return "natural";
  }
}

// TAND-012: chat-length picker — how many exchanges the tandem session runs
// before the max_exchanges cap ends it. Rides the WS URL as `&exchanges=`
// (backend whitelists {5,10,15}). Same persist-and-hydrate idiom as the
// input-mode toggle above.
const EXCHANGES_STORAGE_KEY = "tandem-exchanges-v1";
// PAY-002: 15 coins per exchange — cost labels on the length picker.
const EXCHANGE_OPTIONS = [
  { value: 5, label: "Short · 5 · 75 coins" },
  { value: 10, label: "Classic · 10 · 150 coins" },
  { value: 15, label: "Long · 15 · 225 coins" },
] as const;

function readExchanges(): number {
  try {
    const raw = parseInt(localStorage.getItem(EXCHANGES_STORAGE_KEY) ?? "", 10);
    return raw === 5 || raw === 10 || raw === 15 ? raw : 10;
  } catch {
    return 10;
  }
}

// Draw 3 distinct topics from the pool. Only ever called from the mount
// effect below and the shuffle click handler — never during render — so the
// pick can't disagree with the server-rendered HTML (no hydration mismatch;
// same rule Szenario.tsx follows for its own random picks).
function pickThree(pool: string[]): string[] {
  const remaining = [...pool];
  const picks: string[] = [];
  while (remaining.length > 0 && picks.length < 3) {
    const i = Math.floor(Math.random() * remaining.length);
    picks.push(remaining.splice(i, 1)[0]);
  }
  return picks;
}

export default function TopicScreen({
  partner,
  onStart,
  onSwitchPartner,
}: {
  partner: TandemPartner;
  onStart: (topic: string, mode: InputMode, exchanges: number) => void;
  onSwitchPartner: () => void;
}) {
  const [topics, setTopics] = useState<string[]>([]);
  const [recommended, setRecommended] = useState<string[]>([]);
  const [custom, setCustom] = useState<string>("");
  const [selectedCard, setSelectedCard] = useState<string | null>(null);

  // TAND-003: default to "natural" and hydrate from localStorage in an
  // effect — SSR-safe, same pattern Szenario.tsx uses for its level toggle
  // (reading storage during render would mismatch the server-rendered HTML).
  const [mode, setMode] = useState<InputMode>("natural");

  useEffect(() => {
    if (readMode() === "practice") {
      // SSR-safe hydration from localStorage, same idiom as Szenario.tsx's level toggle.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMode("practice");
    }
  }, []);

  // TAND-012: default to 10 and hydrate from localStorage in an effect —
  // same SSR-safe pattern as the input-mode toggle just above.
  const [exchanges, setExchanges] = useState<number>(10);

  useEffect(() => {
    const stored = readExchanges();
    if (stored !== 10) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setExchanges(stored);
    }
  }, []);

  // Load the topic pool once; seed today's 3 recommendations from it. A
  // fetch failure leaves both lists empty — the free-text field still works,
  // so the screen is never a dead end.
  useEffect(() => {
    let alive = true;
    fetch(`${HTTP_BASE}/tandem/topics?lesson=${encodeURIComponent(partner.lessonId)}`)
      .then((r) => r.json())
      .then((data: { topics?: string[] }) => {
        if (!alive) return;
        const list = data.topics ?? [];
        setTopics(list);
        setRecommended(pickThree(list));
      })
      .catch(() => {
        /* free-text remains available */
      });
    return () => {
      alive = false;
    };
  }, [partner.lessonId]);

  // What the Start button sends: a selected recommendation card takes
  // precedence, falling back to the free-text field. In practice this is
  // always "whichever was touched last" — selecting a card doesn't clear
  // the input, but typing in the input does clear the card selection
  // (see the input's onChange below), so the two can never disagree.
  const effectiveTopic = useMemo(
    () => selectedCard ?? custom.trim(),
    [selectedCard, custom]
  );

  const shuffle = () => {
    setRecommended(pickThree(topics));
    setSelectedCard(null);
  };

  return (
    <div className="relative flex min-h-screen flex-col bg-paper text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-card/85 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link href="/practice" className="flex items-center gap-2.5">
            <Image
              src="/mascot/raven.png"
              alt="Spralingua raven mascot"
              width={40}
              height={40}
              priority
              className="mascot-keyline h-9 w-9 select-none"
            />
            <span className="font-display text-[22px] font-black tracking-tight text-ink">
              Spralingua
            </span>
          </Link>
          <Link
            href="/practice"
            className="font-body text-[13px] font-bold text-ink-soft hover:text-ink"
          >
            ← All modes
          </Link>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-14">
        <div className="rise-in">
          <div className="flex items-center gap-3">
            <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
              Grammatik-Tandem
            </p>
            <button
              type="button"
              onClick={onSwitchPartner}
              className="font-body text-[12px] font-bold text-ink-soft hover:text-ink"
            >
              ← Change partner
            </button>
          </div>
          <h1 className="mt-3 font-display text-[clamp(28px,4.6vw,44px)] font-black leading-[1.03] tracking-tight text-ink">
            What should you and {partner.name} talk about?
          </h1>
          <p className="mt-4 max-w-xl font-body text-[16px] leading-relaxed text-ink-soft">
            Pick a topic to break the ice — or bring your own. {partner.name}{" "}
            chats only in German and remembers your past conversations.
          </p>

          {/* TAND-003: Natural (mic always hot) vs Practice (tap record, tap
              stop, auto-send) input mode — same two-button pill language as
              Szenario's B1/B2 toggle. */}
          <div className="mt-5 flex items-center gap-3">
            <span className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              Input
            </span>
            <div className="inline-flex overflow-hidden rounded-full border-[3px] border-ink">
              {(
                [
                  { key: "natural" as const, label: "Natural" },
                  { key: "practice" as const, label: "Practice" },
                ]
              ).map(({ key, label }) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => {
                    if (key === mode) return;
                    setMode(key);
                    try {
                      localStorage.setItem(MODE_STORAGE_KEY, key);
                    } catch {}
                  }}
                  className={`px-4 py-1.5 font-display text-[12px] font-black uppercase tracking-[0.16em] transition-colors ${
                    key === mode
                      ? "bg-ink text-on-fill"
                      : "bg-card text-ink hover:text-flag-red"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* TAND-012: how many exchanges the chat runs before the cap ends
              it — same pill language as the Input toggle above. */}
          <div className="mt-3 flex items-center gap-3">
            <span className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
              Length
            </span>
            <div className="inline-flex overflow-hidden rounded-full border-[3px] border-ink">
              {EXCHANGE_OPTIONS.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    if (value === exchanges) return;
                    setExchanges(value);
                    try {
                      localStorage.setItem(EXCHANGES_STORAGE_KEY, String(value));
                    } catch {}
                  }}
                  className={`px-4 py-1.5 font-display text-[12px] font-black uppercase tracking-[0.16em] transition-colors ${
                    value === exchanges
                      ? "bg-ink text-on-fill"
                      : "bg-card text-ink hover:text-flag-red"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Today's 3 recommended topics + shuffle */}
        {recommended.length > 0 && (
          <div
            className="rise-in mt-9"
            style={{ animationDelay: "80ms" }}
          >
            <div className="flex items-center justify-between gap-4">
              <p className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted">
                Our recommendation for you today
              </p>
              <button
                type="button"
                onClick={shuffle}
                disabled={topics.length <= 3}
                className="btn-3d shrink-0 rounded-full border-[3px] border-ink bg-card px-4 py-2 font-display text-[12px] font-black uppercase tracking-[0.16em] text-ink disabled:opacity-40"
                style={inkShadow}
              >
                Shuffle
              </button>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              {recommended.map((t) => {
                const selected = selectedCard === t;
                return (
                  <button
                    key={t}
                    type="button"
                    aria-pressed={selected}
                    onClick={() =>
                      setSelectedCard((prev) => (prev === t ? null : t))
                    }
                    className={`rounded-2xl border-[3px] border-ink px-5 py-5 text-left font-display text-[17px] font-black leading-tight transition ${
                      selected
                        ? "bg-ink text-on-fill"
                        : "bg-flag-gold-soft text-ink hover:bg-card hover:text-flag-red"
                    }`}
                    style={inkShadow}
                  >
                    {t}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Free-text */}
        <div className="rise-in mt-7" style={{ animationDelay: "200ms" }}>
          <label
            htmlFor="tandem-custom-topic"
            className="font-body text-[11px] font-bold uppercase tracking-[0.22em] text-ink-muted"
          >
            Or talk about…
          </label>
          <input
            id="tandem-custom-topic"
            type="text"
            value={custom}
            onChange={(e) => {
              setCustom(e.target.value);
              setSelectedCard(null);
            }}
            placeholder="Type your own topic"
            maxLength={120}
            className="mt-3 w-full rounded-2xl border-[3px] border-ink bg-card px-5 py-3.5 font-body text-[16px] text-ink placeholder:text-ink-muted focus:outline-none focus:ring-4 focus:ring-flag-gold-soft"
          />
        </div>

        {/* PAY-002: balance note + insufficient-Start disable */}
        <TopicBalanceNote exchanges={exchanges} />

        {/* Start */}
        <div className="rise-in mt-9" style={{ animationDelay: "260ms" }}>
          <TopicStartButton
            partnerName={partner.name}
            effectiveTopic={effectiveTopic}
            mode={mode}
            exchanges={exchanges}
            onStart={onStart}
          />
        </div>
      </main>
    </div>
  );
}

// PAY-002: one-line balance note below the length picker.
function TopicBalanceNote({ exchanges }: { exchanges: number }) {
  const bal = useCoinBalance();
  const bypassed = useCoinsBypassed();
  if (!bal) return null;
  const cost = exchanges * 15;
  if (bypassed) {
    return (
      <p className="rise-in mt-3 font-body text-[12px] text-ink-muted" style={{ animationDelay: "220ms" }}>
        🪙 developer · this chat is free
      </p>
    );
  }
  return (
    <p className="rise-in mt-3 font-body text-[12px] text-ink-muted" style={{ animationDelay: "220ms" }}>
      🪙 {bal.balance} coins · this chat costs {cost} coins
      {bal.balance < cost && (
        <>
          {" "}
          —{" "}
          <Link href="/pricing" className="font-bold text-flag-red underline underline-offset-2">
            Get more coins
          </Link>
        </>
      )}
    </p>
  );
}

function TopicStartButton({
  partnerName,
  effectiveTopic,
  mode,
  exchanges,
  onStart,
}: {
  partnerName: string;
  effectiveTopic: string;
  mode: "natural" | "practice";
  exchanges: number;
  onStart: (topic: string, mode: "natural" | "practice", exchanges: number) => void;
}) {
  const bal = useCoinBalance();
  // PAY-002: developers are never charged (see useCoinsBypassed) — their
  // balance is frozen, so pricing must never gate their Start button.
  const bypassed = useCoinsBypassed();
  const cost = exchanges * 15;
  const insufficient = !bypassed && bal !== null && bal.balance < cost;
  const disabled = !effectiveTopic || insufficient;
  return (
    <>
      <button
        type="button"
        onClick={() => onStart(effectiveTopic, mode, exchanges)}
        disabled={disabled}
        className="btn-3d inline-flex w-full items-center justify-center gap-2 rounded-2xl border-[3px] border-ink bg-flag-red px-7 py-4 font-display text-[16px] font-black uppercase tracking-[0.14em] text-on-fill disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
        style={inkShadow}
      >
        Start chatting with {partnerName}
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
          Not enough coins for this length —{" "}
          <Link href="/pricing" className="underline underline-offset-2">
            get more coins
          </Link>{" "}
          or pick a shorter chat.
        </p>
      )}
    </>
  );
}
