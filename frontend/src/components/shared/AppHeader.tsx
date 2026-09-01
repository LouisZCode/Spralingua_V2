"use client";

import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import { useAuth } from "@/components/auth/AuthContext";

// APPHDR-001: the one header for every Spralingua screen. Before this
// component existed, ~25 screens each carried a hand-copied inline <header>
// wearing the same skin (sticky top-0 z-50 border-b-[3px] border-line
// bg-card/85 backdrop-blur) — and they had already drifted apart (a smaller
// wordmark on /pricing/success, no mascot in the legal layout, three different
// back-link styles). One skin lives here now.
//
// Anatomy: raven + wordmark on the left, then on the right (in order):
// `right` node(s), the back link, the profile avatar.
//
// - logoHref:  where the wordmark goes. Default "/practice" — on every app
//   screen the learner's home is the practice menu; the landing page passes
//   "/" explicitly (and its logo IS a link now — the logo-clicks-home reflex
//   predates us).
// - back:      the right-side escape link ({href, label}), e.g. drills get
//   { href: "/practice", label: "← Menu" }.
// - right:     extra right-side nodes (toggles, coin chip, level pill…),
//   rendered before the back link and avatar.
// - avatar:    when signed in, a circle button with the learner's picture
//   (Google profile image) or initials, linking to /profile — the account
//   affordance the old headers never had (sign-out used to exist only in the
//   old setup screen). Renders nothing when signed out.
// - sticky:    false for moment pages (PricingSuccess, legal) that scroll
//   away with the content.
// - maxWidth:  the inner container width — "max-w-6xl" on the landing page,
//   "max-w-3xl" on the teacher/legal screens, "max-w-5xl" everywhere else.

type AppHeaderProps = {
  logoHref?: string | null;
  back?: { href: string; label: string };
  right?: ReactNode;
  avatar?: boolean;
  sticky?: boolean;
  maxWidth?: string;
};

const BACK_LINK_CLASS =
  "font-body text-[12px] font-bold uppercase tracking-[0.22em] text-ink transition-colors hover:text-flag-red";

export default function AppHeader({
  logoHref = "/practice",
  back,
  right,
  avatar = true,
  sticky = true,
  maxWidth = "max-w-5xl",
}: AppHeaderProps) {
  const { user } = useAuth();

  const logo = (
    <>
      <Image
        src="/mascot/raven.png"
        alt="Spralingua raven mascot"
        width={40}
        height={40}
        priority
        className="mascot-keyline h-9 w-9 select-none"
      />
      <span className="font-display text-[22px] font-black tracking-tight text-ink">
        Spralingua
      </span>
    </>
  );

  return (
    <header
      className={
        sticky
          ? "sticky top-0 z-50 border-b-[3px] border-line bg-card/85 backdrop-blur"
          : "relative border-b-[3px] border-line bg-card/85 backdrop-blur"
      }
    >
      <div
        className={`mx-auto flex ${maxWidth} items-center justify-between px-6 py-4`}
      >
        {logoHref === null ? (
          <div className="flex items-center gap-2.5">{logo}</div>
        ) : (
          <Link href={logoHref} className="flex items-center gap-2.5">
            {logo}
          </Link>
        )}

        <div className="flex items-center gap-4">
          {right}
          {back && (
            <Link href={back.href} className={BACK_LINK_CLASS}>
              {back.label}
            </Link>
          )}
          {avatar && user && <ProfileAvatar />}
        </div>
      </div>
    </header>
  );
}

// The signed-in account affordance: a circle with the learner's Google
// picture, falling back to their initials. Links to /profile, where account
// things live (plan, level, sign-out). Falls back to "·" when there is
// neither a picture nor a name/email to abbreviate — honest emptiness beats
// a wrong letter.
function ProfileAvatar() {
  const { user } = useAuth();
  if (!user) return null;

  const source = user.name ?? user.email ?? "";
  const initials =
    source
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "·";

  return (
    <Link
      href="/profile"
      aria-label="My profile"
      title="My profile"
      className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full border-[3px] border-line bg-paper-warm transition-transform hover:scale-105"
    >
      {user.picture ? (
        <Image
          src={user.picture}
          alt=""
          width={36}
          height={36}
          unoptimized
          className="h-full w-full object-cover"
        />
      ) : (
        <span className="font-display text-[13px] font-black text-ink">
          {initials}
        </span>
      )}
    </Link>
  );
}
