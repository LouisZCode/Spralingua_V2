"use client";

import { Fragment, useState } from "react";

type PathKey = "A1" | "B1" | "DEV";

type LessonNode = {
  id: string;
  number: string;
  title: string;
  subtitle: string;
  tag?: string;
};

type Path = {
  key: PathKey;
  label: string;
  sublabel: string;
  lessons: LessonNode[];
};

const PATHS: Path[] = [
  {
    key: "A1",
    label: "A1",
    sublabel: "Beginner",
    lessons: [
      {
        id: "a1_l1",
        number: "01",
        title: "Sidewalk Hello",
        subtitle: "First encounters · introducing yourself",
        tag: "respond",
      },
    ],
  },
  {
    key: "B1",
    label: "B1",
    sublabel: "Intermediate",
    lessons: [
      {
        id: "b1_l1",
        number: "01",
        title: "Waiting Room Run-In",
        subtitle: "Small talk while you both wait",
        tag: "respond",
      },
    ],
  },
  {
    key: "DEV",
    label: "Developer",
    sublabel: "Internal tools",
    lessons: [
      {
        id: "lesson_zero",
        number: "00",
        title: "Warmup",
        subtitle: "Open conversation, no goal",
        tag: "conversation",
      },
      {
        id: "goodbye_test",
        number: "✕",
        title: "Goodbye Test",
        subtitle: "Pipeline shutdown drill",
        tag: "respond",
      },
    ],
  },
];

const VOICES: Record<string, { label: string; meta: string }> = {
  German_Female: { label: "Female", meta: "Berlin · clear" },
  "German-Male": { label: "Male", meta: "Munich · warm" },
  luis_clone: { label: "Luis", meta: "Cloned · personal" },
};

export interface SessionParams {
  lesson: string;
  voice: string;
}

export default function SetupView({
  onSubmit,
}: {
  onSubmit: (params: SessionParams) => void;
}) {
  const [pathKey, setPathKey] = useState<PathKey>("A1");
  const path = PATHS.find((p) => p.key === pathKey)!;

  const [lessonId, setLessonId] = useState<string>(path.lessons[0].id);
  const [voice, setVoice] = useState<string>("German_Female");

  const switchPath = (key: PathKey) => {
    const next = PATHS.find((p) => p.key === key)!;
    setPathKey(key);
    setLessonId(next.lessons[0].id);
  };

  const handleStart = () => {
    onSubmit({ lesson: lessonId, voice });
  };

  return (
    <main className="relative min-h-screen overflow-hidden bg-white text-ink">
      {/* Bauhaus decorations */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-44 -right-44 h-[30rem] w-[30rem] rounded-full bg-flag-gold/35"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-24 -left-24 h-72 w-72 rotate-6 bg-flag-red/85"
        style={{ clipPath: "polygon(0 0, 100% 0, 0 100%)" }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute top-[42%] -right-2 h-4 w-44 rotate-[18deg] bg-ink"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute top-24 -left-6 h-24 w-24 rotate-12 border-[3px] border-ink"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-paper-grid opacity-60"
      />

      {/* Content column */}
      <div className="relative mx-auto flex min-h-screen w-full max-w-[560px] flex-col px-6 py-10">
        {/* Header */}
        <header className="rise-in" style={{ animationDelay: "0ms" }}>
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.32em] text-ink-muted">
            Voice · Deutsch · v1
          </p>
          <h1 className="mt-2 font-display text-[52px] leading-[0.95] font-black tracking-tight text-ink">
            <span className="highlighter-gold pr-2">Spralingua</span>
          </h1>
        </header>

        {/* Path tabs */}
        <nav
          className="rise-in mt-9 flex gap-2"
          style={{ animationDelay: "80ms" }}
          aria-label="Lesson paths"
        >
          {PATHS.map((p) => {
            const active = p.key === pathKey;
            return (
              <button
                key={p.key}
                onClick={() => switchPath(p.key)}
                aria-pressed={active}
                className={`group flex-1 rounded-2xl border-[3px] border-ink px-3 py-3 text-left transition-colors ${
                  active
                    ? "cursor-default bg-ink text-white"
                    : "btn-3d bg-white text-ink hover:bg-ink hover:text-white"
                }`}
                style={
                  {
                    ["--shadow-color"]: "var(--color-ink)",
                  } as React.CSSProperties
                }
              >
                <span className="block font-display text-[20px] font-black leading-none">
                  {p.label}
                </span>
                <span
                  className={`mt-1 block font-body text-[10px] font-semibold uppercase tracking-[0.18em] ${
                    active
                      ? "text-white/70"
                      : "text-ink-muted group-hover:text-white/70"
                  }`}
                >
                  {p.sublabel}
                </span>
              </button>
            );
          })}
        </nav>

        {/* Path canvas */}
        <section
          key={pathKey}
          className="rise-in relative mt-20"
          style={{ animationDelay: "160ms" }}
          aria-label={`${path.label} path lessons`}
        >
          <div className="flex flex-col">
            {path.lessons.map((lesson, i) => {
              const selected = lesson.id === lessonId;
              const flip = i % 2 === 1;
              return (
                <Fragment key={lesson.id}>
                  {i > 0 && (
                    <div
                      aria-hidden
                      className="dot-connector mx-auto h-10 w-1"
                    />
                  )}
                  <div
                    className={`flex items-center gap-5 ${
                      flip ? "flex-row-reverse" : ""
                    }`}
                    style={{
                      transform: flip ? "translateX(28px)" : "translateX(-28px)",
                    }}
                  >
                    <NodeTile
                      lesson={lesson}
                      selected={selected}
                      onClick={() => setLessonId(lesson.id)}
                    />
                    <NodeLabel
                      lesson={lesson}
                      selected={selected}
                      flip={flip}
                    />
                  </div>
                </Fragment>
              );
            })}
          </div>
        </section>

        {/* Spacer that grows so footer hugs bottom */}
        <div className="flex-1" />

        {/* Voice picker */}
        <section
          className="rise-in mt-10 rounded-[28px] border-[3px] border-ink bg-paper-warm p-5"
          style={{ animationDelay: "220ms" }}
        >
          <div className="flex items-center justify-between">
            <span className="font-body text-[10px] font-bold uppercase tracking-[0.32em] text-ink">
              Voice
            </span>
            <span className="font-body text-[10px] font-semibold uppercase tracking-[0.2em] text-ink-muted">
              speaks back to you
            </span>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2">
            {Object.entries(VOICES).map(([key, v]) => {
              const active = key === voice;
              return (
                <button
                  key={key}
                  onClick={() => setVoice(key)}
                  className={`rounded-2xl border-[3px] border-ink px-2 py-3 transition-colors ${
                    active
                      ? "cursor-default bg-flag-gold text-ink"
                      : "btn-3d bg-white text-ink hover:bg-flag-gold"
                  }`}
                  style={
                    {
                      ["--shadow-color"]: "var(--color-ink)",
                    } as React.CSSProperties
                  }
                >
                  <span className="block font-display text-[15px] font-bold leading-none">
                    {v.label}
                  </span>
                  <span className="mt-1 block font-body text-[10px] font-medium uppercase tracking-[0.14em] text-ink-soft">
                    {v.meta}
                  </span>
                </button>
              );
            })}
          </div>
        </section>

        {/* CTA */}
        <button
          onClick={handleStart}
          className="btn-3d rise-in mt-5 flex w-full items-center justify-center gap-3 rounded-[28px] border-[3px] border-flag-red-deep bg-flag-red px-6 py-5 font-display text-[18px] font-black uppercase tracking-[0.18em] text-white"
          style={
            {
              ["--shadow-color"]: "var(--color-flag-red-deep)",
              animationDelay: "300ms",
            } as React.CSSProperties
          }
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current">
            <path d="M6 4 L20 12 L6 20 Z" />
          </svg>
          Start lesson
        </button>

        <p
          className="rise-in mt-3 text-center font-body text-[11px] uppercase tracking-[0.22em] text-ink-muted"
          style={{ animationDelay: "340ms" }}
        >
          Mic on · 15-min cap · auto-ends on goodbye
        </p>
      </div>
    </main>
  );
}

function NodeTile({
  lesson,
  selected,
  onClick,
}: {
  lesson: LessonNode;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <div className="relative">
      {selected && (
        <span
          aria-hidden
          className="absolute -top-3 left-1/2 z-10 -translate-x-1/2 rounded-full border-2 border-ink bg-flag-gold px-2 py-0.5 font-body text-[9px] font-bold uppercase tracking-[0.22em] text-ink"
        >
          Selected
        </span>
      )}
      <button
        onClick={onClick}
        aria-pressed={selected}
        className={`btn-3d relative grid h-24 w-24 place-items-center rounded-full border-[4px] transition-colors ${
          selected
            ? "border-flag-red-deep bg-flag-red text-white"
            : "border-ink bg-white text-ink hover:bg-paper-warm"
        }`}
        style={
          {
            ["--shadow-color"]: selected
              ? "var(--color-flag-red-deep)"
              : "var(--color-ink)",
          } as React.CSSProperties
        }
      >
        <span className="font-display text-[26px] font-black leading-none">
          {lesson.number}
        </span>
      </button>
    </div>
  );
}

function NodeLabel({
  lesson,
  selected,
  flip,
}: {
  lesson: LessonNode;
  selected: boolean;
  flip: boolean;
}) {
  return (
    <div
      className={`max-w-[220px] ${flip ? "text-right" : "text-left"}`}
    >
      <h3
        className={`font-display text-[19px] font-bold leading-tight transition-colors ${
          selected ? "text-ink" : "text-ink-soft"
        }`}
      >
        {lesson.title}
      </h3>
      <p
        className={`mt-1 font-body text-[13px] leading-snug ${
          selected ? "text-ink-soft" : "text-ink-muted"
        }`}
      >
        {lesson.subtitle}
      </p>
      {lesson.tag && (
        <span
          className={`mt-2 inline-block rounded-full border px-2 py-0.5 font-body text-[9px] font-bold uppercase tracking-[0.22em] ${
            selected
              ? "border-flag-red bg-flag-red-soft text-flag-red-deep"
              : "border-ink-faint bg-paper text-ink-muted"
          }`}
        >
          {lesson.tag}
        </span>
      )}
    </div>
  );
}
