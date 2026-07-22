import type { Metadata } from "next";
import Genus from "@/components/Genus";

export const metadata: Metadata = {
  title: "Spralingua — Artikel-Anker",
};

export default function GenusPage() {
  return <Genus />;
}
