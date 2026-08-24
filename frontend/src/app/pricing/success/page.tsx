import type { Metadata } from "next";
import { Suspense } from "react";
import PricingSuccess from "@/components/PricingSuccess";

export const metadata: Metadata = {
  title: "Spralingua — Payment received",
};

export default function PricingSuccessPage() {
  // PricingSuccess reads ?session_id= via useSearchParams, which requires a
  // Suspense boundary to opt out of forcing the whole route to client-side
  // rendering at build time.
  return (
    <Suspense fallback={null}>
      <PricingSuccess />
    </Suspense>
  );
}
