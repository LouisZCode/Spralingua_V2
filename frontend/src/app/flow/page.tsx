import type { Metadata } from "next";
import Flow from "@/components/Flow";

export const metadata: Metadata = {
  title: "Spralingua — Flow",
};

export default function FlowPage() {
  return <Flow />;
}
