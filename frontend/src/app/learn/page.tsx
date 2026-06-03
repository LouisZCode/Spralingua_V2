import type { Metadata } from "next";
import VoiceChat from "@/components/VoiceChat";

export const metadata: Metadata = {
  title: "Spralingua — Lesson",
};

export default function LearnPage() {
  return <VoiceChat />;
}
