import type { Metadata } from "next";
import LandingPage from "@/components/LandingPage";

export const metadata: Metadata = {
  title: "Spralingua — Learn a language by speaking it",
  description:
    "Real-time voice conversations with an AI partner. You talk, it listens, it talks back — then shows you how you did.",
};

export default function Home() {
  return <LandingPage />;
}
