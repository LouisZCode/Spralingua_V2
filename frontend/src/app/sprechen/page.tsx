import type { Metadata } from "next";
import Sprechen from "@/components/Sprechen";

export const metadata: Metadata = {
  title: "Spralingua — Sprechen",
};

export default function SprechenPage() {
  return <Sprechen />;
}
