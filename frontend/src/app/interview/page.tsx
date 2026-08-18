import type { Metadata } from "next";
import Interview from "@/components/Interview";

export const metadata: Metadata = {
  title: "Spralingua — Interview",
};

export default function InterviewPage() {
  return <Interview />;
}
