import type { Metadata } from "next";
import Szenario from "@/components/Szenario";

export const metadata: Metadata = {
  title: "Spralingua — Szenario-Sparring",
};

export default function SzenarioPage() {
  return <Szenario />;
}
