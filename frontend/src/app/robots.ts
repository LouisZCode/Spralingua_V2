import type { MetadataRoute } from "next";

// LAND-001: a bare, honest robots.txt — allow everything, no sitemap (none
// exists yet). Absence of this file was one of the "blank invitation link"
// symptoms alongside the missing openGraph/twitter metadata in layout.tsx.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
    },
  };
}
