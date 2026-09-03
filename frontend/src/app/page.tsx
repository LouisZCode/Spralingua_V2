import LandingPage from "@/components/LandingPage";

// LAND-001: no per-page metadata override — `/` inherits the German-first
// title/description/OpenGraph block from app/layout.tsx.

export default function Home() {
  return <LandingPage />;
}
