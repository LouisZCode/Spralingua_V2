import type { Metadata } from "next";
import Pricing from "@/components/Pricing";

export const metadata: Metadata = {
  title: "Spralingua — Pricing",
};

export default function PricingPage() {
  return <Pricing />;
}
