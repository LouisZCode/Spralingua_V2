import type { Metadata } from "next";
import Briefkasten from "@/components/Briefkasten";

export const metadata: Metadata = {
  title: "Spralingua — Briefkasten",
};

export default function BriefkastenPage() {
  return <Briefkasten />;
}
