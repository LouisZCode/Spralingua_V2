import type { Metadata } from "next";
import Zeitfaerbung from "@/components/Zeitfaerbung";

export const metadata: Metadata = {
  title: "Spralingua — Zeitfärbung",
};

export default function ZeitfaerbungPage() {
  return <Zeitfaerbung />;
}
