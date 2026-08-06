import type { Metadata } from "next";
import Satzbau from "@/components/Satzbau";

export const metadata: Metadata = {
  title: "Spralingua — Satzbau",
};

export default function SatzbauPage() {
  return <Satzbau />;
}
