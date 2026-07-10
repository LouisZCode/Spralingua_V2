import type { Metadata } from "next";
import Verbformen from "@/components/Verbformen";

export const metadata: Metadata = {
  title: "Spralingua — Verbformen",
};

export default function VerbformenPage() {
  return <Verbformen />;
}
