// FRONTEND-4 (Sentry triage, 2026-10-25): the app has zero Server Actions,
// so no real browser ever POSTs to a page route — but action-shaped bot
// POSTs to / made Next.js render the error path outside <AuthProvider>,
// where useAuth threw and turned each bot hit into a 500 + Sentry event
// (5 events, all Chrome-131 desktop-Linux scanner builds, 0 users). Kill
// non-GET/HEAD on / at the edge; every other path already gets Next's own
// clean 405 / RSC handling. The pathname check is redundant under the "/"
// matcher but stays as documentation if the matcher ever widens.
import { NextResponse, type NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  if (
    req.method !== "GET" &&
    req.method !== "HEAD" &&
    req.nextUrl.pathname === "/"
  ) {
    return new NextResponse(null, { status: 405 });
  }
  return NextResponse.next();
}

export const config = { matcher: "/" };
