import type { Metadata } from "next";
import Development from "@/components/Development";

export const metadata: Metadata = {
  title: "Spralingua — Your Development",
};

export default function DevelopmentPage() {
  return <Development />;
}
