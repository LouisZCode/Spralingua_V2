import type { Metadata } from "next";
import PracticeMenu from "@/components/PracticeMenu";

export const metadata: Metadata = {
  title: "Spralingua — Choose your practice",
};

export default function PracticePage() {
  return <PracticeMenu />;
}
