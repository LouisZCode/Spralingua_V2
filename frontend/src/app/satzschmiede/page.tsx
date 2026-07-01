import type { Metadata } from "next";
import Satzschmiede from "@/components/Satzschmiede";

export const metadata: Metadata = {
  title: "Spralingua — Vocabulary Practice",
};

export default function SatzschmiedePage() {
  return <Satzschmiede />;
}
