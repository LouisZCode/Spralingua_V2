"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ConversationView from "./ConversationView";
import TeacherTopicScreen from "./TeacherTopicScreen";
import { useAuth } from "./auth/AuthContext";
import { TEACHER_LESSON, TEACHER_VOICE } from "./shared/teacher";
import { HTTP_BASE } from "@/lib/api";

import FaelleTrainer from "./faelle/FaelleTrainer";
import type { CaseItem, CaseVerdict } from "./faelle/api";
import SatzbauTrainer from "./satzbau/SatzbauTrainer";
import type { ClauseItem, ClauseVerdict } from "./satzbau/api";
import VerbindungenTrainer from "./verbindungen/VerbindungenTrainer";
import type { ChunkItem, ChunkVerdict } from "./verbindungen/api";
import BauteilTrainer from "./bauteil/BauteilTrainer";
import type { RoundItem as BauteilItem, BauteilVerdict } from "./bauteil/api";
import ZeitfaerbungTrainer from "./zeitfaerbung/ZeitfaerbungTrainer";
import type { ZeitItem, ZeitVerdict } from "./zeitfaerbung/api";
import SprechenTrainer from "./sprechen/SprechenTrainer";
import type { SpokenTask, SprechenVerdict } from "./sprechen/api";
import ProduceCard from "./teacher/ProduceCard";
import type { ProduceItem, ProduceVerdict } from "./teacher/ProduceCard";

// AGENT-001: the /teacher orchestrator — the explanation-agent counterpart to
// TandemChat. Note 5 added a picker screen (TeacherTopicScreen) ahead of the
// shared ConversationView: auth guard -> topic picker -> conversation, so
// Clara opens inside a topic the app already knew instead of asking for one.
// `typedInput` surfaces the type-a-turn overlay as a first-class button —
// typing is the precise channel for German examples, since the teacher
// session runs English STT.
//
// CLARA-13: this file also owns Clara's interactive-exercise loop — GET the
// item, hold it until it's time to reveal, mount the SAME drill trainer Flow
// mounts (round of one), POST the attempt to the teacher-only endpoint, and
// report the outcome back through the pipeline via the existing
// POST /say/{user_id}. ConversationView only renders whatever opaque
// `exerciseSlot` this file hands it (inline in the chat flow, single-focus
// enforced there) — the choice of trainer, the Skip row, and every attempt/
// give-up closure below all live here. See teacher/routes.py (backend) for
// the two HTTP endpoints.
//
// CLARA-14 Phase A: sprechen joins the roster (round 2's drill — audio, its
// own multipart endpoint), and every mounted trainer now gets `hideContinue`
// — no post-verdict advance click. A graded card stays up showing its
// feedback and dismisses itself either when Clara's next reply reveals
// (onBotReply, wired below) or a 15s backstop, whichever comes first. See
// each closure's own comments below for the specifics.

// Sentinel-OUT: kept byte-identical to agents/pipecat_wrapper.py's
// EXERCISE_RESULT_PREFIX. The backend keys off this exact literal to route a
// /say turn out of the stored transcript and the audio↔text pairing (see
// that file's "AGENT-00X sentinel-IN" comment) — changing one side without
// the other breaks that routing.
const EXERCISE_RESULT_PREFIX = "⟦ÜBUNGSERGEBNIS⟧";

// The backend caps /say bodies at 500 chars (config/settings.py's
// SAY_MAX_CHARS); stay comfortably under that so a report is never the thing
// that gets rejected.
const REPORT_MAX_CHARS = 450;

// The card must appear ~2s after Clara finishes SPEAKING her announcement,
// not the instant the marker arrives (see onExerciseRequest's own comment in
// ConversationView.tsx — it already fires at the "she finished talking"
// bubble-reveal moment, so this is purely the extra pause on top of that).
const EXERCISE_REVEAL_DELAY_MS = 2000;

// CLARA-14: once a graded verdict has been reported to Clara, the card is
// meant to dismiss when her spoken reaction to it reveals (see onBotReply
// below) — but if that reply never arrives (a dropped /say, a session that
// ends some other way before she replies), this backstop clears it anyway
// so a learner is never left staring at a stale feedback card.
const EXERCISE_DISMISS_BACKSTOP_MS = 15000;

function truncateReport(text: string): string {
  return text.length > REPORT_MAX_CHARS
    ? text.slice(0, REPORT_MAX_CHARS - 1) + "…"
    : text;
}

// CLARA-13: the GET /teacher/exercise response — a discriminated union on
// `drill`, `item` typed by each drill's own native round-item shape (the
// exact per-item dict that drill's own /round endpoint emits — see
// clara_trainers_contracts.md's PER-DRILL CONTRACTS). itemId/patternId ride
// alongside `item` rather than inside it — the native item shapes don't
// carry a pattern id of their own.
type Exercise =
  | { drill: "faelle"; itemId: string; patternId: string; item: CaseItem }
  | { drill: "satzbau"; itemId: string; patternId: string; item: ClauseItem }
  | {
      drill: "verbindungen";
      itemId: string;
      patternId: string;
      item: ChunkItem;
    }
  | { drill: "bauteil"; itemId: string; patternId: string; item: BauteilItem }
  | {
      drill: "zeitfaerbung";
      itemId: string;
      patternId: string;
      item: ZeitItem;
    }
  | { drill: "sprechen"; itemId: string; patternId: string; item: SpokenTask }
  // CLARA-16: the live forge card — POST /teacher/exercise/forge's response.
  // No longer a native VERBINDUNGEN gap-fill item (that was CLARA-15); the
  // forge now returns a production task, graded typed OR spoken, mounted by
  // the bespoke ProduceCard below. Keyed by `topic` — the developer's
  // free-text ask — instead of a taxonomy `patternId`, since a forged item
  // has none.
  | { drill: "produce"; itemId: string; topic: string; item: ProduceItem };

// D5: the three report shapes, byte-identical to the pre-CLARA-13 template —
// only the per-drill sentence/answer/expected/note extraction (below) is new.
function buildReport(params: {
  patternId: string;
  sentence: string;
  answer: string;
  expected: string;
  correct: boolean;
  alsoCorrectFit: boolean;
  note: string | null;
}): string {
  const { patternId, sentence, answer, expected, correct, alsoCorrectFit, note } =
    params;
  let report: string;
  if (correct && !alsoCorrectFit) {
    report =
      `${EXERCISE_RESULT_PREFIX} Correct — exercise on ${patternId}: ` +
      `the sentence was "${sentence}", they answered "${answer}".`;
  } else if (correct) {
    report =
      `${EXERCISE_RESULT_PREFIX} Correct — exercise on ${patternId}: ` +
      `the sentence was "${sentence}", they answered "${answer}" — also a correct fit ` +
      `(the reference answer was "${expected}").`;
  } else {
    report =
      `${EXERCISE_RESULT_PREFIX} Wrong — exercise on ${patternId}: ` +
      `the sentence was "${sentence}", they answered "${answer}"; ` +
      `the correct answer is "${expected}".`;
  }
  if (note) report += ` ${note}`;
  return report;
}

// CLARA-14: sprechen's own report template family — no single reference
// answer to quote (D5's alsoCorrectFit doesn't apply to speech), so this
// doesn't reuse buildReport. Pinned per clara_sprechen_spec.md's mapping.
function buildSprechenReport(params: {
  patternId: string;
  prompt: string;
  transcript: string;
  passed: boolean;
  constraintNote: string | null;
  slips: { note: string }[];
}): string {
  const { patternId, prompt, transcript, passed, constraintNote, slips } =
    params;
  let report: string;
  if (passed) {
    report =
      `${EXERCISE_RESULT_PREFIX} Correct — speaking exercise on ${patternId}: ` +
      `the task was "${prompt}", they said "${transcript}".`;
    if (constraintNote) report += ` ${constraintNote}`;
  } else {
    report =
      `${EXERCISE_RESULT_PREFIX} Wrong — speaking exercise on ${patternId}: ` +
      `the task was "${prompt}", they said "${transcript}"` +
      (constraintNote ? `; ${constraintNote}` : ".");
  }
  const slipNotes = slips.slice(0, 2).map((s) => s.note);
  if (slipNotes.length > 0) report += ` Slips: ${slipNotes.join("; ")}.`;
  return report;
}

function buildSprechenGiveUpReport(
  patternId: string,
  prompt: string,
  constraintNote: string | null
): string {
  let report =
    `${EXERCISE_RESULT_PREFIX} Wrong — speaking exercise on ${patternId}: ` +
    `the task was "${prompt}", they gave up.`;
  if (constraintNote) report += ` ${constraintNote}`;
  return report;
}

// CLARA-16: produce's own report template family — labeled by TOPIC, not
// patternId (a forged item has none, same reasoning as CLARA-15's forge
// report before it). No alsoCorrectFit (there's no single reference
// answer — `example` is a model, not THE answer), and "their sentence" is
// either what they typed or, for a spoken attempt, the transcript STT
// heard — the caller passes whichever applies.
function buildProduceReport(params: {
  topic: string;
  task: string;
  answer: string;
  correct: boolean;
  note: string | null;
  corrected: string | null;
  example: string;
}): string {
  const { topic, task, answer, correct, note, corrected, example } = params;
  if (correct) {
    const tip = note ? ` — tip given: ${note}` : "";
    return (
      `${EXERCISE_RESULT_PREFIX} Correct — produce exercise on ${topic}: ` +
      `the task was "${task}", they answered "${answer}"${tip}.`
    );
  }
  return (
    `${EXERCISE_RESULT_PREFIX} Wrong — produce exercise on ${topic}: ` +
    `the task was "${task}", they answered "${answer}" — ${note ?? ""}; ` +
    `a good version: "${corrected ?? example}".`
  );
}

function buildProduceGiveUpReport(
  topic: string,
  task: string,
  example: string
): string {
  return (
    `${EXERCISE_RESULT_PREFIX} Gave up — produce exercise on ${topic}: ` +
    `the task was "${task}"; a good answer: "${example}".`
  );
}

// CLARA-16: attaches the HTTP status to whatever it throws — every existing
// closure below only ever reads `.message` (unchanged), but ProduceCard's
// own catch (network error vs. a 404 expired item) needs the status too, and
// this is the one place both postAttempt/postAttemptAudio funnel through.
type HttpAttemptError = Error & { status?: number };

function httpAttemptError(status: number, message: string): HttpAttemptError {
  const err = new Error(message) as HttpAttemptError;
  err.status = status;
  return err;
}

// D3: the pinned POST /teacher/exercise/attempts payload/response contract —
// a thin fetch, not a per-drill api.ts (that endpoint doesn't exist there:
// Clara's room writes nothing and must not touch the drills' own
// coin-gated /attempts routes). Throws on a non-2xx exactly like every other
// practice client's `request` helper.
async function postAttempt<V>(
  token: string,
  body: {
    drill: string;
    itemId: string;
    give_up?: boolean;
    answer?: string;
    order?: string[];
  }
): Promise<V> {
  const res = await fetch(`${HTTP_BASE}/teacher/exercise/attempts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw httpAttemptError(
      res.status,
      typeof detail === "string" ? detail : "Couldn't check that — try again."
    );
  }
  return res.json() as Promise<V>;
}

// CLARA-14: sprechen's real attempt is audio, not JSON — its own multipart
// endpoint (POST /teacher/exercise/attempts-audio). No Content-Type header:
// the browser sets the multipart boundary itself. Give-up still has no
// audio, so it goes through postAttempt above like every other drill.
// CLARA-16: extended with a `drill` field, ALWAYS appended now that a second
// drill (produce) uses this same endpoint — the backend defaults a missing
// one to "sprechen", but both call sites below send it explicitly rather
// than lean on that default. Generic over the verdict shape so produce's
// {…, transcript} return type doesn't have to masquerade as SprechenVerdict.
async function postAttemptAudio<V>(
  token: string,
  itemId: string,
  audio: Blob,
  drill: string
): Promise<V> {
  const form = new FormData();
  form.append("itemId", itemId);
  form.append("audio", audio, "attempt");
  form.append("drill", drill);
  const res = await fetch(`${HTTP_BASE}/teacher/exercise/attempts-audio`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw httpAttemptError(
      res.status,
      typeof detail === "string" ? detail : "Couldn't check that — try again."
    );
  }
  return res.json() as Promise<V>;
}

export default function TeacherChat() {
  const { token, user, ready } = useAuth();
  const router = useRouter();
  // null = still picking; "" is a valid choice (the "just want to talk"
  // escape hatch), so the gate below is `=== null`, not falsiness.
  const [topic, setTopic] = useState<string | null>(null);
  // Cold-start slice: the picked focus/starter card's taxonomy pattern id,
  // rides the WS URL as `&pattern=` (see ConversationView). Undefined for a
  // free-text topic or "I just want to talk" — those carry no pattern id.
  const [pattern, setPattern] = useState<string | undefined>(undefined);
  const handleTopicStart = useCallback((t: string, patternId?: string) => {
    setPattern(patternId);
    setTopic(t);
  }, []);

  // Same guard the /learn and /tandem routes use: once hydration settles, a
  // missing token bounces to the public landing page, where sign-in lives.
  useEffect(() => {
    if (ready && !token) {
      router.replace("/");
    }
  }, [ready, token, router]);

  // ─── CLARA-13: the exercise loop ─────────────────────────────────────
  const [exercise, setExercise] = useState<Exercise | null>(null);
  // Bumps on every accepted exercise_request so the mounted trainer remounts
  // with fresh internal state (answering, not a stale verdict) even if a
  // marker ever repeated the same pattern id back to back.
  const [exerciseKey, setExerciseKey] = useState(0);
  // Gates the wrapper row's Skip affordance — true once a graded verdict has
  // landed for the current item, mirroring the old card's "Skip is gone once
  // graded — the card is answered at that point, there's nothing left to
  // skip" rule. Reset on every new exercise; NOT set on a zeitfaerbung
  // "unrecognized" reply (D4 — that's not a graded attempt).
  const [exerciseAnswered, setExerciseAnswered] = useState(false);
  // CLARA-15: non-null while a forge fetch is in flight (holds the topic
  // being forged, before the item exists) — distinct from `exercise`, which
  // only ever holds a MOUNTABLE item. Drives the "Building your exercise…"
  // loading state in the slot (see exerciseSlot below). Null for every
  // other drill's request, which never has a loading phase of its own.
  const [forging, setForging] = useState<string | null>(null);
  // Guards a fetch in flight from a request that's since been superseded (a
  // NEW exercise_request, or the session ending) — only the most recent
  // request's resolution is allowed to touch state.
  const requestSeqRef = useRef(0);
  // The ~2s post-speech pause before a fetched item is allowed to reveal —
  // see EXERCISE_REVEAL_DELAY_MS above and handleExerciseRequest below.
  const revealTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearRevealTimer = useCallback(() => {
    if (revealTimerRef.current) {
      clearTimeout(revealTimerRef.current);
      revealTimerRef.current = null;
    }
  }, []);

  // CLARA-14: the dismissal backstop — armed the instant a graded verdict's
  // report goes out (see sendGradedReport below), cleared the instant
  // something else clears the exercise first (Clara's own reply via
  // onBotReply, a fresh exercise_request, or the session ending).
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearDismissTimer = useCallback(() => {
    if (dismissTimerRef.current) {
      clearTimeout(dismissTimerRef.current);
      dismissTimerRef.current = null;
    }
  }, []);

  const sendReport = useCallback(
    async (rawText: string) => {
      if (!token || !user) return;
      try {
        await fetch(`${HTTP_BASE}/say/${encodeURIComponent(user.id)}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ text: truncateReport(rawText) }),
        });
      } catch {
        // Best-effort — Clara simply never learns how it went. Nothing here
        // is worth surfacing to the learner; the graded verdict itself
        // already rendered from the /teacher/exercise/attempts response.
      }
    },
    [token, user]
  );

  // CLARA-14: the dismissal rework — a graded verdict's report is sent
  // exactly as before (sendReport, unconditionally, same timing), but now
  // also arms the 15s backstop timer (see EXERCISE_DISMISS_BACKSTOP_MS)
  // that stands in for onFlowDone as this card's eventual dismissal.
  // zeitfaerbung's "unrecognized" reply never reaches this — see D4's guard
  // in submitZeitfaerbung below, which returns before this would be called.
  const armDismissBackstop = useCallback(() => {
    clearDismissTimer();
    dismissTimerRef.current = setTimeout(() => {
      dismissTimerRef.current = null;
      setExercise(null);
    }, EXERCISE_DISMISS_BACKSTOP_MS);
  }, [clearDismissTimer]);

  const sendGradedReport = useCallback(
    (rawText: string) => {
      void sendReport(rawText);
      armDismissBackstop();
    },
    [sendReport, armDismissBackstop]
  );

  // CLARA-14: the other half of the dismissal — ConversationView's onBotReply
  // fires the instant ANY bot reply reveals (marker or not, see that
  // component's own doc comment). Once a graded verdict is up
  // (exerciseAnswered), Clara's next reply IS her reaction to the report we
  // just sent — that's the natural dismissal moment. Before a verdict lands
  // (exerciseAnswered still false — the reveal-pause window, or the learner
  // still working the item), a bot reply is unrelated to this exercise and
  // must not touch it.
  const handleBotReply = useCallback(() => {
    if (exerciseAnswered) {
      clearDismissTimer();
      setExercise(null);
    }
  }, [exerciseAnswered, clearDismissTimer]);

  const handleExerciseRequest = useCallback(
    (patternId: string) => {
      // A fresh request always replaces whatever's open, and cancels any
      // reveal still pending from a previous one — and, CLARA-14, any
      // dismissal backstop still armed from the PREVIOUS exercise's graded
      // verdict (that card is gone now regardless of the timer).
      const seq = ++requestSeqRef.current;
      clearRevealTimer();
      clearDismissTimer();
      setExercise(null);
      setExerciseAnswered(false);
      // CLARA-15: a fresh drill request always wins over a still-forging card.
      setForging(null);
      if (!token) return;

      // TIMING: this call fires the instant Clara's bubble for THIS reply
      // reveals (ConversationView's flushPendingBot) — i.e. the moment she
      // finishes speaking. The slot itself must not appear until
      // EXERCISE_REVEAL_DELAY_MS after that, so it waits on BOTH the fetch
      // AND this pause, whichever finishes last — fetching starts right away
      // so the data is normally already in hand once the pause elapses.
      let fetched: Exercise | null = null;
      let pauseDone = false;
      const reveal = () => {
        if (requestSeqRef.current !== seq || !fetched || !pauseDone) return;
        setExercise(fetched);
        setExerciseAnswered(false);
        setExerciseKey((k) => k + 1);
      };
      revealTimerRef.current = setTimeout(() => {
        revealTimerRef.current = null;
        pauseDone = true;
        reveal();
      }, EXERCISE_REVEAL_DELAY_MS);

      fetch(
        `${HTTP_BASE}/teacher/exercise?pattern=${encodeURIComponent(patternId)}`,
        { headers: { Authorization: `Bearer ${token}` } }
      )
        .then(async (res) => {
          if (requestSeqRef.current !== seq) return; // superseded
          if (res.status === 404) {
            void sendReport(
              `${EXERCISE_RESULT_PREFIX} No exercise was available for pattern ${patternId}.`
            );
            return;
          }
          if (!res.ok) return; // nothing actionable to show or report
          const data = (await res.json()) as Exercise;
          fetched = data;
          reveal();
        })
        .catch(() => {
          // Network error fetching the item — same "nothing actionable" call
          // as a non-ok, non-404 response; the pending pause simply never
          // has anything to reveal.
        });
    },
    [token, sendReport, clearRevealTimer, clearDismissTimer]
  );

  // CLARA-16: the live forge — POST /teacher/exercise/forge, then mount
  // ProduceCard (below), which owns the typed/spoken UI for the returned
  // production task. Expect multi-second latency (two LLM calls
  // server-side): the fetch, not the ~2s reveal pause, is normally the long
  // pole, so the "Building your exercise…" loading state (see exerciseSlot
  // below) covers that wait. A 403 (a stray message reaching a non-dev —
  // server bug), 502, or any network failure fails safe — Clara already
  // spoke, so v1 has no error card: the slot just clears silently and the
  // failure goes to the console.
  const handleExerciseForge = useCallback(
    (topic: string) => {
      // A fresh forge always replaces whatever's open (drill or another
      // forge), same as handleExerciseRequest above.
      const seq = ++requestSeqRef.current;
      clearRevealTimer();
      clearDismissTimer();
      setExercise(null);
      setExerciseAnswered(false);
      setForging(topic);
      if (!token) {
        setForging(null);
        return;
      }

      let fetched: Extract<Exercise, { drill: "produce" }> | null = null;
      let pauseDone = false;
      const reveal = () => {
        if (requestSeqRef.current !== seq || !fetched || !pauseDone) return;
        setExercise(fetched);
        setExerciseAnswered(false);
        setForging(null);
        setExerciseKey((k) => k + 1);
      };
      revealTimerRef.current = setTimeout(() => {
        revealTimerRef.current = null;
        pauseDone = true;
        reveal();
      }, EXERCISE_REVEAL_DELAY_MS);

      fetch(`${HTTP_BASE}/teacher/exercise/forge`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ topic }),
      })
        .then(async (res) => {
          if (requestSeqRef.current !== seq) return; // superseded
          if (!res.ok) {
            console.warn(`Forge exercise failed: ${res.status}`);
            setForging(null);
            return;
          }
          const data = (await res.json()) as Extract<
            Exercise,
            { drill: "produce" }
          >;
          fetched = data;
          reveal();
        })
        .catch((e) => {
          if (requestSeqRef.current !== seq) return; // superseded
          console.warn("Forge exercise fetch error:", e);
          setForging(null);
        });
    },
    [token, clearRevealTimer, clearDismissTimer]
  );

  const handleSkip = useCallback(() => {
    // CLARA-15: the slot can be up either as a live/graded drill (`exercise`)
    // or as the forge loading state (`forging` — no item yet, just the topic
    // being forged) — Skip must work from either.
    if (!exercise && !forging) return;
    requestSeqRef.current++; // supersede any fetch that might still land
    const label = exercise
      ? exercise.drill === "produce"
        ? exercise.topic
        : exercise.patternId
      : forging!;
    void sendReport(
      `${EXERCISE_RESULT_PREFIX} They skipped the exercise on ${label}.`
    );
    setExercise(null);
    setForging(null);
  }, [exercise, forging, sendReport]);

  // CLARA-14: every mount below now passes `hideContinue`, which makes each
  // trainer's `advance()`/`next()` unreachable (no button, keybinding
  // ignored — see each trainer's own gate), so this never actually fires
  // from any of the mounts today (seven, since CLARA-15's forge card). Kept
  // wired anyway as a harmless fallback: a trainer mounted without
  // `hideContinue` would still dismiss correctly through its normal
  // onFlowDone path, same as Flow's.
  const handleExerciseDone = useCallback(() => {
    setExercise(null);
  }, []);

  // Round of one, never mutated, never re-dealt — flow-mode trainers never
  // call this, same noop Flow.tsx wires.
  const noopNewRound = useCallback(() => {}, []);

  // Session winding down (WS closed / Finish confirmed) — drop any open
  // slot silently, whatever state it's in, and cancel a reveal still
  // pending in its ~2s window so it can never pop up after the fact.
  const handleSessionEnded = useCallback(() => {
    requestSeqRef.current++;
    clearRevealTimer();
    clearDismissTimer(); // CLARA-14: no dangling dismiss backstop post-session
    setExercise(null);
    setExerciseAnswered(false);
    setForging(null); // CLARA-15: including an in-flight forge fetch's slot
  }, [clearRevealTimer, clearDismissTimer]);

  // Belt-and-suspenders: clear both timers if this component itself
  // unmounts mid-window (e.g. a fast route change) — no orphaned timers.
  useEffect(() => {
    return () => {
      clearRevealTimer();
      clearDismissTimer();
    };
  }, [clearRevealTimer, clearDismissTimer]);

  // ─── D5 per-drill report mapping + POST closures ─────────────────────
  // Each of the five mirrors the SAME shape: POST the attempt to the
  // teacher-only endpoint, mark the item answered (so the Skip row above
  // disappears), build the ⟦ÜBUNGSERGEBNIS⟧ report from that drill's native
  // verdict fields, send it, and hand the native verdict back to the
  // trainer — same timing as before (Clara reacts while the card still
  // shows its own feedback).

  const submitFaelle = useCallback(
    async (
      ex: Extract<Exercise, { drill: "faelle" }>,
      answer: string | null,
      giveUp: boolean
    ): Promise<CaseVerdict> => {
      if (!token) throw new Error("Not signed in.");
      const verdict = await postAttempt<CaseVerdict>(token, {
        drill: "faelle",
        itemId: ex.itemId,
        give_up: giveUp,
        answer: giveUp ? undefined : answer ?? "",
      });
      setExerciseAnswered(true);
      const displayAnswer = giveUp ? "(gave up)" : answer ?? "";
      // Old adapter's rule: meansInstead rides along on the SAME note line.
      let note = verdict.note;
      if (verdict.meansInstead) {
        note = note ? `${note} (${verdict.meansInstead})` : verdict.meansInstead;
      }
      const alsoCorrectFit =
        verdict.correct && displayAnswer.trim() !== verdict.expected.trim();
      sendGradedReport(
        buildReport({
          patternId: ex.patternId,
          sentence: ex.item.frame,
          answer: displayAnswer,
          expected: verdict.expected,
          correct: verdict.correct,
          alsoCorrectFit,
          note,
        })
      );
      return verdict;
    },
    [token, sendGradedReport]
  );

  const submitSatzbau = useCallback(
    async (
      ex: Extract<Exercise, { drill: "satzbau" }>,
      order: string[] | null,
      giveUp: boolean
    ): Promise<ClauseVerdict> => {
      if (!token) throw new Error("Not signed in.");
      const verdict = await postAttempt<ClauseVerdict>(token, {
        drill: "satzbau",
        itemId: ex.itemId,
        give_up: giveUp,
        order: giveUp ? undefined : order ?? [],
      });
      setExerciseAnswered(true);
      // The target sentence — given lead-in + the canonical order — doubles
      // as both S and E for satzbau (there's no separate gap-fill "sentence"
      // text to quote).
      const sentence = [ex.item.given, ...verdict.expected]
        .filter(Boolean)
        .join(" ");
      const displayAnswer = giveUp
        ? "(gave up)"
        : [ex.item.given, ...(order ?? [])].filter(Boolean).join(" ");
      // Mirrors the old adapter's collapse: `variant` only ever accompanies
      // a TRUE verdict, `note` only a FALSE one — never both.
      const note = verdict.correct ? verdict.variant : verdict.note;
      const alsoCorrectFit = verdict.correct && verdict.variant != null;
      sendGradedReport(
        buildReport({
          patternId: ex.patternId,
          sentence,
          answer: displayAnswer,
          expected: sentence,
          correct: verdict.correct,
          alsoCorrectFit,
          note,
        })
      );
      return verdict;
    },
    [token, sendGradedReport]
  );

  const submitVerbindungen = useCallback(
    async (
      ex: Extract<Exercise, { drill: "verbindungen" }>,
      answer: string | null,
      giveUp: boolean
    ): Promise<ChunkVerdict> => {
      if (!token) throw new Error("Not signed in.");
      const verdict = await postAttempt<ChunkVerdict>(token, {
        drill: "verbindungen",
        itemId: ex.itemId,
        give_up: giveUp,
        answer: giveUp ? undefined : answer ?? "",
      });
      setExerciseAnswered(true);
      const displayAnswer = giveUp ? "(gave up)" : answer ?? "";
      const alsoCorrectFit =
        verdict.correct && displayAnswer.trim() !== verdict.expected.trim();
      sendGradedReport(
        buildReport({
          patternId: ex.patternId,
          sentence: ex.item.frame,
          answer: displayAnswer,
          expected: verdict.expected,
          correct: verdict.correct,
          alsoCorrectFit,
          note: verdict.note,
        })
      );
      return verdict;
    },
    [token, sendGradedReport]
  );

  const submitBauteil = useCallback(
    async (
      ex: Extract<Exercise, { drill: "bauteil" }>,
      answer: string | null,
      giveUp: boolean
    ): Promise<BauteilVerdict> => {
      if (!token) throw new Error("Not signed in.");
      const verdict = await postAttempt<BauteilVerdict>(token, {
        drill: "bauteil",
        itemId: ex.itemId,
        give_up: giveUp,
        answer: giveUp ? undefined : answer ?? "",
      });
      setExerciseAnswered(true);
      const displayAnswer = giveUp ? "(gave up)" : answer ?? "";
      const alsoCorrectFit =
        verdict.correct && displayAnswer.trim() !== verdict.expected.trim();
      sendGradedReport(
        buildReport({
          patternId: ex.patternId,
          sentence: ex.item.frame,
          answer: displayAnswer,
          expected: verdict.expected,
          correct: verdict.correct,
          alsoCorrectFit,
          note: verdict.note,
        })
      );
      return verdict;
    },
    [token, sendGradedReport]
  );

  const submitZeitfaerbung = useCallback(
    async (
      ex: Extract<Exercise, { drill: "zeitfaerbung" }>,
      answer: string | null,
      giveUp: boolean
    ): Promise<ZeitVerdict> => {
      if (!token) throw new Error("Not signed in.");
      const verdict = await postAttempt<ZeitVerdict>(token, {
        drill: "zeitfaerbung",
        itemId: ex.itemId,
        give_up: giveUp,
        answer: giveUp ? undefined : answer ?? "",
      });
      // D4: "unrecognized" isn't a scored attempt anywhere in this product —
      // the trainer keeps the item live for another try (no advance), and
      // Clara must hear nothing about it. Give-up never resolves this kind
      // (zeitfaerbung/routes.py), so this only ever short-circuits a genuine
      // typo/gibberish text attempt; Skip stays available since nothing was
      // graded.
      if (verdict.kind === "unrecognized") return verdict;
      setExerciseAnswered(true);
      const displayAnswer = giveUp ? "(gave up)" : answer ?? "";
      const alsoCorrectFit =
        verdict.correct && displayAnswer.trim() !== verdict.expected.trim();
      sendGradedReport(
        buildReport({
          patternId: ex.patternId,
          sentence: ex.item.frame,
          answer: displayAnswer,
          expected: verdict.expected,
          correct: verdict.correct,
          alsoCorrectFit,
          note: verdict.note,
        })
      );
      return verdict;
    },
    [token, sendGradedReport]
  );

  // CLARA-14: sprechen's own submit — real attempt goes through the
  // multipart audio endpoint, give-up through the same JSON endpoint every
  // other drill's give-up uses (D3 in the backend spec: drill="sprechen" +
  // give_up=true is the one JSON shape sprechen's attempts route accepts).
  // Report mapping is sprechen's own template family (buildSprechenReport /
  // buildSprechenGiveUpReport above) — no alsoCorrectFit, no single
  // reference answer to quote.
  const submitSprechen = useCallback(
    async (
      ex: Extract<Exercise, { drill: "sprechen" }>,
      audio: Blob | null,
      giveUp: boolean
    ): Promise<SprechenVerdict> => {
      if (!token) throw new Error("Not signed in.");
      const verdict = giveUp
        ? await postAttempt<SprechenVerdict>(token, {
            drill: "sprechen",
            itemId: ex.itemId,
            give_up: true,
          })
        : await postAttemptAudio<SprechenVerdict>(
            token,
            ex.itemId,
            audio ?? new Blob(),
            "sprechen"
          );
      setExerciseAnswered(true);
      sendGradedReport(
        giveUp
          ? buildSprechenGiveUpReport(
              ex.patternId,
              ex.item.prompt,
              verdict.constraintNote
            )
          : buildSprechenReport({
              patternId: ex.patternId,
              prompt: ex.item.prompt,
              transcript: verdict.transcript,
              passed: verdict.passed,
              constraintNote: verdict.constraintNote,
              slips: verdict.slips,
            })
      );
      return verdict;
    },
    [token, sendGradedReport]
  );

  // CLARA-16: the forge/produce card's own submit trio — typed (JSON,
  // mirrors every other drill's postAttempt shape), spoken (multipart,
  // carries its own transcript back so the report can quote what was
  // actually said), give-up (JSON, no text of the learner's own to quote).
  // Three separate closures rather than one `(answer, giveUp)` pair like the
  // other drills above — ProduceCard's own prop contract keeps typed/
  // spoken/give-up as three distinct callbacks (see that file), so this
  // mirrors it 1:1 instead of collapsing them. Report is labeled by TOPIC,
  // not patternId — same reasoning as CLARA-15's forge report before it, a
  // forged item has no taxonomy pattern id.
  const submitProduceAnswer = useCallback(
    async (
      ex: Extract<Exercise, { drill: "produce" }>,
      answer: string
    ): Promise<ProduceVerdict> => {
      if (!token) throw new Error("Not signed in.");
      const verdict = await postAttempt<ProduceVerdict>(token, {
        drill: "produce",
        itemId: ex.itemId,
        answer,
      });
      setExerciseAnswered(true);
      sendGradedReport(
        buildProduceReport({
          topic: ex.topic,
          task: ex.item.task,
          answer,
          correct: verdict.correct,
          note: verdict.note,
          corrected: verdict.corrected,
          example: verdict.example,
        })
      );
      return verdict;
    },
    [token, sendGradedReport]
  );

  const submitProduceAudio = useCallback(
    async (
      ex: Extract<Exercise, { drill: "produce" }>,
      audio: Blob
    ): Promise<ProduceVerdict & { transcript: string }> => {
      if (!token) throw new Error("Not signed in.");
      const verdict = await postAttemptAudio<ProduceVerdict & { transcript: string }>(
        token,
        ex.itemId,
        audio,
        "produce"
      );
      setExerciseAnswered(true);
      sendGradedReport(
        buildProduceReport({
          topic: ex.topic,
          task: ex.item.task,
          answer: verdict.transcript,
          correct: verdict.correct,
          note: verdict.note,
          corrected: verdict.corrected,
          example: verdict.example,
        })
      );
      return verdict;
    },
    [token, sendGradedReport]
  );

  const submitProduceGiveUp = useCallback(
    async (
      ex: Extract<Exercise, { drill: "produce" }>
    ): Promise<ProduceVerdict> => {
      if (!token) throw new Error("Not signed in.");
      const verdict = await postAttempt<ProduceVerdict>(token, {
        drill: "produce",
        itemId: ex.itemId,
        give_up: true,
      });
      setExerciseAnswered(true);
      sendGradedReport(
        buildProduceGiveUpReport(ex.topic, ex.item.task, verdict.example)
      );
      return verdict;
    },
    [token, sendGradedReport]
  );
  // ───────────────────────────────────────────────────────────────────

  // CLARA-13: the wrapper row (D6's Skip affordance) plus whichever real
  // drill trainer this pattern dealt, mounted exactly as Flow.tsx mounts it
  // (round of one, `flow`, `allowGiveUp`, a noop onNewRound) minus
  // onGloss/onAdd/onNudge/sessionId — see clara_trainers_contracts.md's
  // "Flow's mount reference". Handed to ConversationView as one opaque node;
  // it renders it, we own everything inside it.
  const exerciseSlot: React.ReactNode = exercise ? (
    <div className="flex w-full max-w-[440px] flex-col gap-2">
      <div className="flex items-center justify-between gap-3 rounded-[16px] border-[3px] border-line bg-paper-warm px-4 py-2">
        <span className="font-body text-[10px] font-black uppercase tracking-[0.28em] text-ink-muted">
          Quick practice
        </span>
        {/* Unobtrusive — never a rival to the trainer's own Check button,
            and gone once graded (D6/old card's rule: there's nothing left
            to skip). */}
        {!exerciseAnswered && (
          <button
            type="button"
            onClick={handleSkip}
            className="font-body text-[10px] font-bold uppercase tracking-[0.2em] text-ink-faint underline-offset-2 hover:text-ink-muted hover:underline disabled:pointer-events-none disabled:opacity-40"
          >
            Skip
          </button>
        )}
      </div>
      {exercise.drill === "faelle" && (
        <FaelleTrainer
          key={exerciseKey}
          round={[exercise.item]}
          flow
          onNewRound={noopNewRound}
          allowGiveUp
          onAttempt={(_itemId, answer) => submitFaelle(exercise, answer, false)}
          onGiveUp={() => submitFaelle(exercise, null, true)}
          onFlowDone={handleExerciseDone}
          hideContinue
        />
      )}
      {exercise.drill === "satzbau" && (
        <SatzbauTrainer
          key={exerciseKey}
          round={[exercise.item]}
          flow
          onNewRound={noopNewRound}
          allowGiveUp
          onAttempt={(_itemId, order) => submitSatzbau(exercise, order, false)}
          onGiveUp={() => submitSatzbau(exercise, null, true)}
          onFlowDone={handleExerciseDone}
          hideContinue
        />
      )}
      {exercise.drill === "verbindungen" && (
        <VerbindungenTrainer
          key={exerciseKey}
          round={[exercise.item]}
          flow
          onNewRound={noopNewRound}
          allowGiveUp
          onAttempt={(_itemId, answer) =>
            submitVerbindungen(exercise, answer, false)
          }
          onGiveUp={() => submitVerbindungen(exercise, null, true)}
          onFlowDone={handleExerciseDone}
          hideContinue
        />
      )}
      {exercise.drill === "bauteil" && (
        <BauteilTrainer
          key={exerciseKey}
          round={[exercise.item]}
          flow
          onNewRound={noopNewRound}
          allowGiveUp
          onAttempt={(_itemId, answer) => submitBauteil(exercise, answer, false)}
          onGiveUp={() => submitBauteil(exercise, null, true)}
          onFlowDone={handleExerciseDone}
          hideContinue
        />
      )}
      {exercise.drill === "zeitfaerbung" && (
        <ZeitfaerbungTrainer
          key={exerciseKey}
          round={[exercise.item]}
          flow
          onNewRound={noopNewRound}
          allowGiveUp
          onAttempt={(_itemId, answer) =>
            submitZeitfaerbung(exercise, answer, false)
          }
          onGiveUp={() => submitZeitfaerbung(exercise, null, true)}
          onFlowDone={handleExerciseDone}
          hideContinue
        />
      )}
      {exercise.drill === "sprechen" && (
        <SprechenTrainer
          key={exerciseKey}
          round={[exercise.item]}
          flow
          onNewRound={noopNewRound}
          allowGiveUp
          onAttempt={(_taskId, audio) => submitSprechen(exercise, audio, false)}
          onGiveUp={() => submitSprechen(exercise, null, true)}
          onFlowDone={handleExerciseDone}
          hideContinue
        />
      )}
      {/* CLARA-16: the forge card mounts ProduceCard — a bespoke single-item
          UI (typed textarea OR mic), not a round-of-one of an existing
          trainer, so it doesn't take the flow/allowGiveUp/hideContinue
          contract the mounts above do; TeacherChat still owns dismissal via
          exerciseAnswered/onBotReply/the backstop timer exactly the same
          way (see submitProduce* above, which call setExerciseAnswered). */}
      {exercise.drill === "produce" && (
        <ProduceCard
          key={exerciseKey}
          item={exercise.item}
          topic={exercise.topic}
          onAttempt={(answer) => submitProduceAnswer(exercise, answer)}
          onAttemptAudio={(audio) => submitProduceAudio(exercise, audio)}
          onGiveUp={() => submitProduceGiveUp(exercise)}
        />
      )}
    </div>
  ) : forging ? (
    // CLARA-15: the forge fetch is in flight (two LLM calls server-side) —
    // same slot header/Skip row as the graded state above, plus a muted
    // loading line in place of the trainer. Existing tokens only.
    <div className="flex w-full max-w-[440px] flex-col gap-2">
      <div className="flex items-center justify-between gap-3 rounded-[16px] border-[3px] border-line bg-paper-warm px-4 py-2">
        <span className="font-body text-[10px] font-black uppercase tracking-[0.28em] text-ink-muted">
          Quick practice
        </span>
        <button
          type="button"
          onClick={handleSkip}
          className="font-body text-[10px] font-bold uppercase tracking-[0.2em] text-ink-faint underline-offset-2 hover:text-ink-muted hover:underline"
        >
          Skip
        </button>
      </div>
      <p className="text-center font-body text-[13px] font-semibold text-ink-muted">
        Building your exercise…
      </p>
    </div>
  ) : null;

  if (!ready || !token) {
    return null;
  }

  if (topic === null) {
    return <TeacherTopicScreen onStart={handleTopicStart} />;
  }

  return (
    <ConversationView
      params={{ lesson: TEACHER_LESSON, voice: TEACHER_VOICE, topic, pattern }}
      // AGENT-001 note 1: the teacher room is never open-mic — the learner
      // needs time to think before speaking to a teacher. No natural/practice
      // toggle here on purpose, unlike TopicScreen.
      practiceMode
      typedInput
      // AGENT-001: Clara's `kickoff` key means she speaks first — lock Record
      // until her opening line finishes, see ConversationView's agentOpens.
      agentOpens
      // Clara skips the briefing/"scene preview" screen — the topic screen
      // (TeacherTopicScreen) already served that purpose. Straight into the
      // live phase, auto-connecting on mount.
      skipBriefing
      onFinish={() => router.push("/practice")}
      // Backing out of the briefing card returns to the picker, not /practice
      // (mirrors TandemChat's onBack -> setTopic(null)).
      onBack={() => setTopic(null)}
      onExerciseRequest={handleExerciseRequest}
      onExerciseForge={handleExerciseForge}
      onBotReply={handleBotReply}
      onSessionEnded={handleSessionEnded}
      // CLARA-13: ConversationView renders whatever this is, inline after
      // the last bubble, once it goes non-null — see that file for
      // placement/animation/single-focus. This file only decides WHEN that
      // happens (handleExerciseRequest above) and what's inside it.
      exerciseSlot={exerciseSlot}
    />
  );
}
