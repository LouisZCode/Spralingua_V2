import type { Metadata } from "next";
import Verbindungen from "@/components/Verbindungen";

export const metadata: Metadata = {
  title: "Spralingua — Feste Verbindungen",
};

export default function VerbindungenPage() {
  return <Verbindungen />;
}
