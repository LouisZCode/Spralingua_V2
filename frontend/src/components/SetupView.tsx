"use client";

import { useState } from "react";

const LEVELS = {
  A1: { label: "A1 - Beginner", situations: { introducing_yourself: "Introducing Yourself" } },
  B2: { label: "B2 - Upper Intermediate", situations: { planning_picnic: "Planning a Picnic" } },
} as const;

// Keys must match services/tts.py::VOICE_MAP on the backend.
const VOICES: Record<string, string> = {
  German_Female: "German Female",
  "German-Male": "German Male",
  luis_clone: "Luis (Clone)",
};

// Keys must match a YAML in agents/prompts/{lesson_id}.yaml on the backend.
const LESSONS: Record<string, string> = {
  lesson_zero: "Lesson 0",
  goodbye_test: "Goodbye Test (dev)",
  a1_l1: "A1-L1 — Sidewalk Hello",
};

export interface SessionParams {
  lesson: string;
  level: string;
  situation: string;
  voice: string;
}

export default function SetupView({
  onSubmit,
}: {
  onSubmit: (params: SessionParams) => void;
}) {
  const [lesson, setLesson] = useState<string>("lesson_zero");
  const [level, setLevel] = useState<keyof typeof LEVELS>("A1");
  const [situation, setSituation] = useState<string>("introducing_yourself");
  const [voice, setVoice] = useState<string>("German_Female");

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-full max-w-md rounded-xl bg-slate-800 p-8">
        <h1 className="mb-2 text-center text-2xl font-bold">
          Spralingua
        </h1>
        <p className="mb-6 text-center text-sm text-slate-400">
          Select your class for today.
        </p>

        <div className="mb-3">
          <label className="mb-1 block text-xs text-slate-400">Lesson</label>
          <select
            value={lesson}
            onChange={(e) => setLesson(e.target.value)}
            className="w-full rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-200"
          >
            {Object.entries(LESSONS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <div className="mb-6 grid grid-cols-3 gap-3">
          <div>
            <label className="mb-1 block text-xs text-slate-400">Level</label>
            <select
              value={level}
              onChange={(e) => {
                const v = e.target.value as keyof typeof LEVELS;
                setLevel(v);
                setSituation(Object.keys(LEVELS[v].situations)[0]);
              }}
              className="w-full rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-200"
            >
              {Object.entries(LEVELS).map(([key, val]) => (
                <option key={key} value={key}>
                  {val.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Situation</label>
            <select
              value={situation}
              onChange={(e) => setSituation(e.target.value)}
              className="w-full rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-200"
            >
              {Object.entries(LEVELS[level].situations).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Voice</label>
            <select
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              className="w-full rounded-lg bg-slate-700 px-3 py-2 text-sm text-slate-200"
            >
              {Object.entries(VOICES).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <button
          onClick={() => onSubmit({ lesson, level, situation, voice })}
          className="w-full rounded-lg bg-green-500 py-3 font-semibold text-slate-900 hover:bg-green-400"
        >
          Continue
        </button>
      </div>
    </div>
  );
}
