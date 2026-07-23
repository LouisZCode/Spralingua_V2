"use client";

// The vocab nudge pill — a retrieval cue, deliberately count-first: it only
// says HOW MANY of the learner's own deck words would fit here, so they
// rummage through memory before clicking reveals which (click = graduated
// hint, not the answer). Decorative by contract: callers' fetch functions
// already resolve to [] on any failure, so this component never surfaces an
// error state, only "no pill".
//
// Parents control per-item remounting via the React `key` prop (e.g.
// `key={item.id}`) — a fresh key tears this instance down and mounts a new
// one, which fetches again for the new item. This component itself never
// refetches on re-render.
import { useEffect, useRef, useState } from "react";

export type NudgeWord = { word: string; hint: string };

export default function VocabNudgePill({
  fetch,
}: {
  fetch: () => Promise<NudgeWord[]>;
}) {
  const [words, setWords] = useState<NudgeWord[] | null>(null);
  const [show, setShow] = useState(false);

  // Latest fetch prop, read via a ref so the mount effect below has an
  // empty dep array and only ever fires once per mount — never on re-render.
  // Same "latest ref" idiom as GenusTrainer's submitDropRef / Glossable's
  // timer refs — safe because current is only ever read inside the effect
  // below, never during render.
  const fetchRef = useRef(fetch);
  // eslint-disable-next-line react-hooks/refs -- latest-ref idiom, see above
  fetchRef.current = fetch;

  useEffect(() => {
    let stale = false;
    fetchRef.current()
      .then((res) => {
        if (!stale) setWords(res);
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, []);

  if (!words || words.length === 0) return null;

  return (
    <div className="mt-3 text-center">
      <button
        type="button"
        onClick={() => setShow((v) => !v)}
        aria-expanded={show}
        className="inline-flex items-center gap-1.5 rounded-full border-2 border-flag-gold-deep bg-flag-gold-soft px-3.5 py-1.5 font-body text-[12px] font-bold text-flag-gold-deep transition-colors hover:bg-flag-gold/30"
      >
        💡 {words.length}{" "}
        {words.length === 1 ? "word" : "words"} from your
        vocabulary would fit here
      </button>
      {show && (
        <div className="mx-auto mt-3 max-w-[420px] rounded-[20px] border-[3px] border-flag-gold-deep bg-flag-gold-soft p-4 text-left">
          <p className="font-body text-[11px] font-black uppercase tracking-[0.2em] text-flag-gold-deep">
            Aus deinem Wortschatz
          </p>
          <ul className="mt-2 space-y-1.5">
            {words.map((w) => (
              <li
                key={w.word}
                className="font-body text-[13px] leading-snug text-ink"
              >
                <span className="font-bold">{w.word}</span>
                <span className="mx-1.5 text-ink-muted">—</span>
                <span className="italic text-ink-soft">{w.hint}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
