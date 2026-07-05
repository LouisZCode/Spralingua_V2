import type { Metadata } from "next";
import TandemChat from "@/components/TandemChat";

export const metadata: Metadata = {
  title: "Spralingua — Tandem Partner",
};

export default function TandemPage() {
  return <TandemChat />;
}
