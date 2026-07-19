"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth/AuthContext";
import ZeitfaerbungTrainer from "./zeitfaerbung/ZeitfaerbungTrainer";
import {
  fetchRound,
  submitAttempt,
  type ZeitItem,
  type ZeitVerdict,
} from "./zeitfaerbung/api";
import { UnauthorizedError } from "./satzschmiede/api";

const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

// Zeitfaerbung — war / wurde / blieb: German splits "was" into a state, a
// becoming, and a staying (plus wurde's second job as the passive auxiliary).
// The gap never signals which one it wants; the learner has to read the
// meaning. Auth-guarded page shell + round state (mirrors Verbindungen.tsx);
// the trainer remounts per round.
export default function Zeitfaerbung() {
  const { token, ready, signOut } = useAuth();
  const router = useRouter();

  const [round, setRound] = useState<ZeitItem[] | null>(null); // null = loading
  const [roundKey, setRoundKey] = useState(0); // remounts the trainer per round
  const [error, setError] = useState(false);
  const [showExplainer, setShowExplainer] = useState(false);

  // One Langfuse Session per practice sitting (OBS-007): minted lazily on the
  // first attempt, held for the whole page visit above the per-round remounts.
  const practiceSessionRef = useRef<string | null>(null);

  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  const loadRound = useCallback(() => {
    if (!token) return;
    setRound(null);
    fetchRound(token)
      .then((items) => {
        setRound(items);
        setRoundKey((k) => k + 1);
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          signOut();
        } else {
          setError(true);
        }
      });
  }, [token, signOut]);

  useEffect(() => {
    loadRound();
  }, [loadRound]);

  const handleAttempt = useCallback(
    async (itemId: string, answer: string): Promise<ZeitVerdict> => {
      if (!token) throw new UnauthorizedError("/zeitfaerbung/attempts");
      practiceSessionRef.current ??=
        "zf-" + crypto.randomUUID().replace(/-/g, "");
      try {
        return await submitAttempt(
          token,
          itemId,
          answer,
          practiceSessionRef.current
        );
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          signOut();
        }
        throw e;
      }
    },
    [token, signOut]
  );

  if (!ready || !token) {
    return null;
  }

  return (
    <div className="relative flex min-h-screen flex-col bg-white text-ink">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-50"
      />

      <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-2.5">
            <Image
              src="/mascot/raven.png"
              alt="Spralingua raven mascot"
              width={40}
              height={40}
              priority
              className="h-9 w-9 select-none"
            />
            <span className="font-display text-[22px] font-black tracking-tight text-ink">
              Spralingua
            </span>
          </Link>
          <Link
            href="/practice"
            className="font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red"
          >
            ← Menu
          </Link>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-6 py-12">
        <div className="mb-5 text-center">
          <h1 className="font-display text-[24px] font-black tracking-tight text-ink">
            Zeitfärbung
          </h1>
          <p className="mt-1.5 font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            war · wurde · blieb
          </p>
          <button
            type="button"
            onClick={() => setShowExplainer((v) => !v)}
            aria-expanded={showExplainer}
            className="btn-3d mt-4 inline-flex items-center gap-2 rounded-full border-[3px] border-ink bg-white px-4 py-2 font-display text-[11px] font-black uppercase tracking-[0.16em] text-ink"
            style={inkShadow}
          >
            Warum diese Übung?
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`h-3.5 w-3.5 transition-transform ${
                showExplainer ? "rotate-180" : ""
              }`}
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </div>

        {showExplainer && (
          <div className="mb-8">
            <ZeitfaerbungExplainer />
          </div>
        )}

        {error ? (
          <p className="text-center font-body text-[14px] font-semibold text-flag-red-deep">
            Couldn&apos;t load a round — is the backend running?
          </p>
        ) : round === null ? null : (
          <ZeitfaerbungTrainer
            key={roundKey}
            round={round}
            onAttempt={handleAttempt}
            onNewRound={loadRound}
          />
        )}
      </main>
    </div>
  );
}

// ─── "Warum diese Übung?" explainer ──────────────────────────────────────
// Collapsed by default (toggled from the header above). Four numbered
// sections walking through why war/wurde/blieb is a meaning decision, not a
// grammar one — the copy is verbatim from the drill design brief.

const DECISION_ROWS: { question: string; example: string; answer: string }[] = [
  {
    question: "Someone doing something TO you? (an action)",
    example: "Ich ___ gefragt / eingeladen.",
    answer: "wurde + Partizip",
  },
  {
    question: "Changing INTO a state? (becoming)",
    example: "Plötzlich ___ es dunkel.",
    answer: "wurde",
  },
  {
    question: "Simply BEING in a state?",
    example: "Ich ___ ein Kind. · Es ___ spät.",
    answer: "war",
  },
  {
    question: "STAYING in a state?",
    example: "Trotz allem ___ ich ruhig.",
    answer: "blieb",
  },
];

const KOPULAS: {
  verb: string;
  form: string;
  label: string;
  example: string;
}[] = [
  { verb: "sein", form: "war", label: "being", example: "Ich war müde." },
  {
    verb: "werden",
    form: "wurde",
    label: "becoming",
    example: "Ich wurde müde. — got tired",
  },
  {
    verb: "bleiben",
    form: "blieb",
    label: "staying",
    example: "Ich blieb ruhig. — stayed calm",
  },
];

const KONJUGATION: { verb: string; forms: string }[] = [
  {
    verb: "werden",
    forms:
      "ich/er/es/sie(sg.) wurde · du wurdest · wir/sie(pl.) wurden · ihr wurdet",
  },
  { verb: "sein", forms: "war · warst · war · waren · wart · waren" },
  { verb: "bleiben", forms: "blieb · bliebst · blieb · blieben" },
];

const EINDEUTIG: { label: string; answer: string; example: string }[] = [
  { label: "Zustand", answer: "war", example: "Es war Montag." },
  { label: "Übergang", answer: "wurde", example: "Plötzlich wurde es dunkel." },
  {
    label: "Passiv",
    answer: "wurde + Part.",
    example: "Das Haus wurde vom Architekten renoviert.",
  },
  {
    label: "Verbleib",
    answer: "blieb",
    example: "Trotz des Stresses blieb ich ruhig.",
  },
];

const DOPPELDEUTIG: { pair: { sentence: string; meaning: string }[] }[] = [
  {
    pair: [
      { sentence: "Ich war krank", meaning: "the state" },
      { sentence: "Ich wurde krank", meaning: "the onset" },
    ],
  },
  {
    pair: [
      { sentence: "Die Tür war geschlossen", meaning: "the result" },
      { sentence: "Die Tür wurde geschlossen", meaning: "the action" },
    ],
  },
];

function SectionHeader({ n, title }: { n: string; title: string }) {
  return (
    <div className="flex items-baseline gap-3">
      <span className="font-display text-[13px] font-black tracking-wide text-flag-red">
        {n}
      </span>
      <h3 className="font-display text-[17px] font-black tracking-tight text-ink">
        {title}
      </h3>
    </div>
  );
}

function ZeitfaerbungExplainer() {
  return (
    <div
      className="rounded-[28px] border-[3px] border-ink bg-white p-7"
      style={inkShadow}
    >
      <p className="font-body text-[11px] font-bold uppercase tracking-[0.24em] text-ink-muted">
        Warum diese Übung?
      </p>

      <div className="mt-5 space-y-7">
        {/* 01 */}
        <div className="border-b-[3px] border-ink pb-7">
          <SectionHeader n="01" title="Warum diese Übung" />
          <p className="mt-3 font-body text-[14px] leading-relaxed text-ink-soft">
            English collapses several meanings into one word — &ldquo;was.&rdquo;
            German splits them, and the split is semantic, not grammatical:
            the same blank takes a different verb depending on whether
            something simply was a certain way, became that way, had it done
            to it, or stayed that way. The classic trap: &ldquo;I was
            teased&rdquo; feels like <em>war</em> (was), but it&apos;s an
            action done to you → <em>wurde gehänselt</em>. And the deeper
            skill is recognizing the many cases where both <em>war</em> and{" "}
            <em>wurde</em> are correct and change the meaning.
          </p>
        </div>

        {/* 02 */}
        <div className="border-b-[3px] border-ink pb-7">
          <SectionHeader n="02" title="Die Entscheidung — vier Fragen" />
          <div className="mt-4 space-y-3">
            {DECISION_ROWS.map((r) => (
              <div
                key={r.question}
                className="rounded-[16px] border-[2px] border-ink bg-paper-warm p-4"
              >
                <p className="font-body text-[13px] font-bold text-ink">
                  {r.question}
                </p>
                <p className="mt-1 font-body text-[13px] italic text-ink-soft">
                  {r.example}
                </p>
                <span className="mt-2 inline-flex items-center rounded-full border-[2px] border-ink bg-white px-3 py-1 font-display text-[12px] font-black text-ink">
                  {r.answer}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* 03 */}
        <div className="border-b-[3px] border-ink pb-7">
          <SectionHeader n="03" title="Die Verben — drei Kopulas" />
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {KOPULAS.map((k) => (
              <div
                key={k.verb}
                className="rounded-[16px] border-[2px] border-ink bg-white p-4 text-center"
              >
                <p className="font-body text-[11px] font-bold uppercase tracking-[0.16em] text-ink-muted">
                  {k.verb} → {k.label}
                </p>
                <p className="mt-1.5 font-display text-[18px] font-black text-ink">
                  {k.form}
                </p>
                <p className="mt-1.5 font-body text-[13px] leading-snug text-ink-soft">
                  {k.example}
                </p>
              </div>
            ))}
          </div>

          <div className="mt-4 rounded-[16px] border-[2px] border-flag-gold-deep bg-flag-gold-soft p-4">
            <p className="font-body text-[11px] font-black uppercase tracking-[0.18em] text-flag-gold-deep">
              werden&apos;s double duty
            </p>
            <p className="mt-1.5 font-body text-[14px] leading-relaxed text-ink">
              It&apos;s also the passive auxiliary:{" "}
              <em>Ich wurde gefragt</em> = someone asked me. One word, two
              jobs.
            </p>
          </div>

          <div className="mt-4 space-y-1.5">
            {KONJUGATION.map((k) => (
              <p key={k.verb} className="font-body text-[13px] text-ink-soft">
                <span className="font-bold text-ink">{k.verb}</span> →{" "}
                {k.forms}
              </p>
            ))}
          </div>
        </div>

        {/* 04 */}
        <div>
          <SectionHeader n="04" title="Satztypen" />

          <p className="mt-4 font-body text-[12px] font-black uppercase tracking-[0.18em] text-ink-muted">
            Eindeutig — one forced answer
          </p>
          <div className="mt-2 space-y-2">
            {EINDEUTIG.map((e) => (
              <div
                key={e.label}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[14px] border-[2px] border-ink bg-white px-4 py-2.5"
              >
                <span className="font-body text-[11px] font-bold uppercase tracking-[0.14em] text-ink-muted">
                  {e.label}
                </span>
                <span className="font-body text-[13px] text-ink-soft">
                  {e.example}
                </span>
                <span className="ml-auto rounded-full border-[2px] border-ink bg-paper-warm px-2.5 py-0.5 font-display text-[12px] font-black text-ink">
                  {e.answer}
                </span>
              </div>
            ))}
          </div>

          <p className="mt-5 font-body text-[12px] font-black uppercase tracking-[0.18em] text-ink-muted">
            Doppeldeutig — two valid answers, different meaning
          </p>
          <div className="mt-2 space-y-2">
            {DOPPELDEUTIG.map((d, i) => (
              <div
                key={i}
                className="rounded-[14px] border-[2px] border-ink bg-white px-4 py-3"
              >
                {d.pair.map((p) => (
                  <p
                    key={p.sentence}
                    className="font-body text-[13px] leading-relaxed text-ink"
                  >
                    <span className="font-bold">{p.sentence}</span>
                    <span className="text-ink-muted"> — {p.meaning}</span>
                  </p>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
