"use client";

import { useState, type CSSProperties, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./AuthContext";
import SignInModal from "./SignInModal";

// Drop-in replacement for the landing page's "Start" <Link>s. Instead of
// navigating straight to /learn, it gates on auth: already signed in → go to
// /learn; otherwise open the Google sign-in modal and only advance once the
// session JWT is in hand. className/style/children are passed through so each
// call site keeps its existing look (nav link, hero button, closing button).
export default function StartCta({
  className,
  style,
  children,
}: {
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  const { token, ready } = useAuth();
  const router = useRouter();
  const [showModal, setShowModal] = useState(false);

  function handleClick() {
    // Wait for localStorage hydration before deciding — otherwise a signed-in
    // user who clicks during the first render would wrongly see the modal.
    if (!ready) return;
    if (token) {
      router.push("/learn");
    } else {
      setShowModal(true);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        className={className}
        style={style}
      >
        {children}
      </button>
      {showModal && (
        <SignInModal
          onSuccess={() => {
            setShowModal(false);
            router.push("/learn");
          }}
          onClose={() => setShowModal(false)}
        />
      )}
    </>
  );
}
