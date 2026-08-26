"use client";

import { useState } from "react";
import { useRecorder } from "../shared/recorder";
import { diffTokens, MarkedText } from "../shared/feedback";
import { diffWords, segmentTranscript } from "../sprechen/slipDiff";
import { InsufficientCoinsError } from "@/lib/coins";
import { OutOfCoinsPanel, refreshCoins } from "../shared/Coins";
import type {
  AnswerResult,
  Brief,
  GoalCoverageItem,
  GrammarVerdict,
  IdiomVerdict,
} from "./api";

const redShadow = {
  ["--shadow-color"]: "var(--color-flag-red-deep)",
} as React.CSSProperties;
const inkShadow = {
  ["--shadow-color"]: "var(--color-ink)",
} as React.CSSProperties;

type AnsweredResult = Extract<AnswerResult, { kind: "answered" }>;

// Round 2 ("read & answer") — ported from interview_local/web/answer.js +
// the round-2 chrome in player.js::enterRound2. Unlike round 1, the
// transcript (rendered by the parent's <Transcript>, above this panel) and
// the brief's question/goals are visible up front; the learner answers the
// question out loud and gets grammar + "the German way" + (when the brief
// has goals) goal-coverage feedback back. Record stays available after a
// verdict for another take — this is a repeatable loop, not a two-attempt
// gate like round 1.
//
// There is no persisted attempt history here (interview/routes.py writes
// nothing per-attempt beyond the silent grammar-ledger harvest — no
// `attempt_index`, unlike the workbench's local JSON store) — only the
// latest take's feedback is ever shown.
export default function Round2Panel({
  chunkId,
  brief,
  round1Passed,
  onBeforeRecord,
  onSubmit,
}: {
  chunkId: string;
  brief: Brief | null;
  // null = no round 1 happened (a legacy, brief-less chunk).
  round1Passed: boolean | null;
  onBeforeRecord: () => void;
  onSubmit: (chunkId: string, audio: Blob) => Promise<AnswerResult>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [softError, setSoftError] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [insufficient, setInsufficient] = useState<{ needed: number; available: number } | null>(null);
  const [result, setResult] = useState<AnsweredResult | null>(null);

  async function handleStop(blob: Blob) {
    setSubmitting(true);
    setSoftError(null);
    setFailed(null);
    try {
      const res = await onSubmit(chunkId, blob);
      if (res.kind === "no_speech") {
        setSoftError("We couldn't hear anything — try again.");
        return;
      }
      setResult(res);
    } catch (err) {
      if (err instanceof InsufficientCoinsError) {
        setInsufficient({ needed: err.needed, available: err.available });
        refreshCoins();
      } else {
        setFailed(
          err instanceof Error && err.message && !err.message.includes("failed (")
            ? err.message
            : "Couldn't check that — try again in a moment."
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  const recorder = useRecorder(handleStop);

  function startRecording() {
    setSoftError(null);
    setFailed(null);
    setResult(null); // a fresh take replaces the last verdict, same as SprechenTrainer's retry
    onBeforeRecord();
    void recorder.start();
  }

  // Only an EXTRACTED question was actually spoken in this chunk's own audio
  // (already heard in round 1) — a "generated" one is the brief-writer's
  // invented round-2 follow-up the learner never heard, so only THAT gets
  // its own card (interview_local/web/player.js::enterRound2).
  const showQuestionCard =
    !!brief?.question && brief.question.source === "generated" && !!brief.question.text;
  const goals = brief?.goals ?? [];

  return (
    <div
      className="rounded-[28px] border-[3px] border-ink bg-card p-7"
      style={inkShadow}
    >
      <p className="text-center font-body text-[11px] font-bold uppercase tracking-[0.24em] text-flag-red">
        {round1Passed === null
          ? "Answer"
          : round1Passed
            ? "Round 2 · Answer"
            : "Round 2 · Answer — comprehension not passed"}
      </p>

      {showQuestionCard && (
        <div className="mt-4 rounded-[18px] border-[3px] border-ink bg-paper-warm px-4 py-3">
          <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
            Question
          </p>
          <p className="mt-1 font-body text-[15px] font-bold leading-relaxed text-ink">
            {brief?.question?.text}
          </p>
        </div>
      )}

      {goals.length > 0 && (
        <div className="mt-4">
          <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
            Your answer should
          </p>
          <GoalsChecklist goals={goals} coverage={result?.goalCoverage ?? undefined} />
          {result?.goalCoverageError && (
            <p className="mt-1.5 font-body text-[12px] text-ink-muted">
              Goal check unavailable.
            </p>
          )}
        </div>
      )}

      <div className="mt-6 flex flex-col items-center gap-3">
        {submitting ? (
          <p className="font-body text-[14px] font-semibold text-ink-muted">
            Checking…
          </p>
        ) : (
          <>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={recorder.recording ? recorder.stop : startRecording}
                className={`btn-3d inline-flex items-center gap-2 rounded-[20px] border-[3px] px-7 py-3.5 font-display text-[14px] font-black uppercase tracking-[0.16em] ${
                  recorder.recording
                    ? "animate-pulse border-flag-red-deep bg-card text-flag-red"
                    : "border-flag-red-deep bg-flag-red text-on-fill"
                }`}
                style={redShadow}
              >
                {recorder.recording ? `Stop · ${recorder.elapsed}s` : "Record"}
              </button>
              {recorder.recording && (
                <button
                  type="button"
                  onClick={recorder.cancel}
                  aria-label="Discard recording"
                  title="Discard recording"
                  className="btn-3d inline-flex h-[52px] w-[52px] items-center justify-center rounded-[20px] border-[3px] border-ink bg-card font-display text-[18px] font-black text-ink"
                  style={inkShadow}
                >
                  ✕
                </button>
              )}
            </div>
            <p className="font-body text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-faint">
              {recorder.recording
                ? "speak, then tap stop — ✕ discards"
                : "answer out loud, in German"}
            </p>
          </>
        )}
        {insufficient && (
          <div className="mt-3">
            <OutOfCoinsPanel needed={insufficient.needed} available={insufficient.available} onDismiss={() => setInsufficient(null)} />
          </div>
        )}
        {(recorder.error || failed || softError) && (
          <p className="text-center font-body text-[13px] font-semibold text-flag-red-deep">
            {recorder.error || failed || softError}
          </p>
        )}
      </div>

      {result && (
        <div className="mt-6 space-y-4">
          <GrammarCard
            transcript={result.transcript}
            grammar={result.grammar}
            grammarError={result.grammarError}
          />
          <IdiomCard
            transcript={result.transcript}
            idiom={result.idiom}
            idiomError={result.idiomError}
          />
        </div>
      )}
    </div>
  );
}

// Goal coverage checklist — same undecided/covered/not-covered shape as
// briefkasten/BriefTrainer.tsx's PointsChecklist, extended with a per-item
// note (interview/judges/goal_coverage.py always returns one). Never a
// strikethrough on an uncovered goal — the standing house rule.
function GoalsChecklist({
  goals,
  coverage,
}: {
  goals: string[];
  coverage?: GoalCoverageItem[];
}) {
  return (
    <ul className="mt-2 space-y-2">
      {goals.map((g, i) => {
        // Same order as the input goals list, one-to-one — the judge's own
        // contract (interview/judges/goal_coverage.py).
        const item = coverage?.[i];
        const known = coverage !== undefined;
        return (
          <li
            key={i}
            className="flex items-start gap-2.5 font-body text-[13px] leading-snug"
          >
            <span
              aria-hidden
              className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border-[2px] font-black text-[11px] ${
                !known
                  ? "border-ink/25 text-transparent"
                  : item?.covered
                    ? "border-success bg-success text-on-fill"
                    : "border-flag-red bg-card text-flag-red"
              }`}
            >
              {known ? (item?.covered ? "✓" : "!") : "·"}
            </span>
            <span className={known && !item?.covered ? "text-ink" : "text-ink-soft"}>
              {g}
              {known && !item?.covered && item?.note && (
                <span className="mt-0.5 block font-body text-[12px] italic text-ink-muted">
                  {item.note}
                </span>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// Grammar card: the learner's own transcript with each slip's quoted span
// marked red (interview_local/web/answer.js::renderGrammarAppliedHtml
// replaced the quote with the correction and colored the replacement red;
// this instead keeps the learner's own words on screen and marks them —
// never a strikethrough, house rule — then shows each slip's minimal
// repair as its own card, reusing the exact diff/anchor helpers
// SprechenTrainer already uses for the same "quote -> corrected, why" shape
// (sprechen/slipDiff.ts).
function GrammarCard({
  transcript,
  grammar,
  grammarError,
}: {
  transcript: string;
  grammar: GrammarVerdict | null;
  grammarError: string | null;
}) {
  if (!grammar && !grammarError) return null;
  const clean = !grammar || grammar.ok || grammar.slips.length === 0;
  const slipQuotes = grammar && !clean ? grammar.slips.map((s) => s.quote) : [];
  const { segments } = segmentTranscript(transcript, slipQuotes, []);

  return (
    <div className="rounded-[18px] border-[3px] border-ink bg-card px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
          Grammar
        </p>
        {grammar && clean && (
          <span className="rounded-full border-2 border-success bg-success-soft px-2 py-0.5 font-body text-[10px] font-black uppercase tracking-[0.1em] text-success">
            ✓ correct
          </span>
        )}
      </div>
      {grammar?.coachNote && (
        <p className="mt-1 font-body text-[12px] leading-snug text-ink-soft">
          {grammar.coachNote}
        </p>
      )}
      {grammarError ? (
        <p className="mt-2 font-body text-[13px] text-ink-muted">
          Grammar check unavailable.
        </p>
      ) : (
        <>
          <p className="mt-2 font-body text-[15px] leading-relaxed text-ink">
            {segments.map((seg, i) =>
              seg.kind === "plain" ? (
                <span key={i}>{seg.text}</span>
              ) : (
                <span
                  key={i}
                  className="rounded-[6px] bg-flag-red-soft px-1 py-0.5 box-decoration-clone"
                >
                  {seg.text}
                  <sup className="ml-1 inline-flex h-[15px] w-[15px] items-center justify-center rounded-full bg-flag-red font-body text-[9px] font-black text-on-fill">
                    {(seg.index ?? 0) + 1}
                  </sup>
                </span>
              )
            )}
          </p>
          {grammar && grammar.slips.length > 0 && (
            <ul className="mt-3 space-y-2.5">
              {grammar.slips.map((s, i) => (
                <li
                  key={i}
                  className="rounded-[14px] border-[2px] border-ink/15 bg-paper-warm px-3 py-2.5"
                >
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-flag-red font-body text-[10px] font-black text-on-fill">
                      {i + 1}
                    </span>
                    <div>
                      <p className="font-body text-[14px] leading-relaxed text-ink">
                        {diffWords(s.quote, s.corrected).map((t, k) =>
                          t.kind === "ghost" ? (
                            <span
                              key={k}
                              className="mr-1.5 align-middle font-body text-[11px] font-semibold text-ink-faint"
                            >
                              ×{t.word}
                            </span>
                          ) : (
                            <span
                              key={k}
                              className={
                                t.kind === "added"
                                  ? "mr-1.5 font-black underline decoration-success decoration-[2.5px] underline-offset-4"
                                  : "mr-1.5"
                              }
                            >
                              {t.word}
                            </span>
                          )
                        )}
                      </p>
                      <p className="mt-1 font-body text-[12px] font-semibold text-ink-soft">
                        <span className="mr-1.5 font-body text-[10px] font-black uppercase tracking-[0.18em] text-flag-red-deep">
                          why
                        </span>
                        {s.note}
                      </p>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

// "How would a German say it?" card — idiom.germanVersion diffed against
// the transcript, changed words BLUE (never red/green: this isn't a
// verdict, a native just phrases it differently — same convention as
// shared/GermanWay.tsx and Briefkasten's natural-version toggle). An
// unchanged rewrite with no suggestions reads as a quiet confirmation.
function IdiomCard({
  transcript,
  idiom,
  idiomError,
}: {
  transcript: string;
  idiom: IdiomVerdict | null;
  idiomError: string | null;
}) {
  if (!idiom && !idiomError) return null;
  const diff = idiom
    ? diffTokens(transcript, idiom.germanVersion, { caseInsensitive: true })
    : null;
  const hasChanges = diff ? diff.corrected.some((t) => t.changed) : false;

  return (
    <div className="rounded-[18px] border-[3px] border-ink bg-paper-warm px-4 py-3">
      <p className="font-body text-[10px] font-black uppercase tracking-[0.22em] text-ink-muted">
        How would a German say it?
      </p>
      {idiom?.coachNote && (
        <p className="mt-1 font-body text-[12px] leading-snug text-ink-soft">
          {idiom.coachNote}
        </p>
      )}
      {idiomError ? (
        <p className="mt-2 font-body text-[13px] text-ink-muted">
          German-way suggestions unavailable.
        </p>
      ) : idiom && diff ? (
        hasChanges || idiom.suggestions.length > 0 ? (
          <>
            <p className="mt-2 font-body text-[15px] leading-relaxed text-ink">
              <MarkedText tokens={diff.corrected} mark="blue" />
            </p>
            {idiom.suggestions.length > 0 && (
              <ul className="mt-3 space-y-2.5">
                {idiom.suggestions.map((s, i) => (
                  <li
                    key={i}
                    className="rounded-[14px] border-[2px] border-ink/15 bg-card px-3 py-2.5"
                  >
                    <p className="font-body text-[13px] italic leading-snug text-ink-soft">
                      &ldquo;{s.original}&rdquo;
                    </p>
                    <p className="mt-1 font-body text-[15px] font-bold text-gender-blue">
                      → {s.germanWay}
                    </p>
                    <p className="mt-1 font-body text-[12px] leading-snug text-ink-muted">
                      {s.why}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className="mt-2 font-body text-[12px] font-bold uppercase tracking-[0.16em] text-success">
            ✓ Already sounds German
          </p>
        )
      ) : null}
    </div>
  );
}
