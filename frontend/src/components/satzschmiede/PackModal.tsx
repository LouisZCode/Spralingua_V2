"use client";

import { createPortal } from "react-dom";
import AddWordForm from "./AddWordForm";
import PackGallery from "./PackGallery";

// "Add Cards" popup — forge a single word (top) or add a whole pack (below),
// in an overlay instead of a full-page view so the trainer stays the main
// surface. Same portal pattern as SignInModal (portal to <body> to escape
// transformed ancestors).
export default function PackModal({
  token,
  onPoolChanged,
  onUnauthorized,
  onClose,
  canPractice,
}: {
  token: string;
  onPoolChanged: () => void;
  onUnauthorized: () => void;
  onClose: () => void;
  // Pool non-empty → the gallery's "Practice your pool →" CTA closes the modal.
  canPractice: boolean;
}) {
  return createPortal(
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-ink/60 p-6 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-[28px] border-[3px] border-ink bg-white p-8 shadow-[0_8px_0_var(--color-ink)]"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute right-4 top-4 grid h-8 w-8 place-items-center rounded-full border-[2px] border-ink text-ink transition-colors hover:bg-ink hover:text-white"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.5}
            strokeLinecap="round"
            className="h-4 w-4"
          >
            <path d="M6 6 L18 18 M18 6 L6 18" />
          </svg>
        </button>

        <h2 className="text-center font-display text-[26px] font-black leading-tight text-ink">
          Add Cards
        </h2>
        <p className="mx-auto mt-2 max-w-[340px] text-center font-body text-[14px] leading-relaxed text-ink-soft">
          Forge a single word or grab a whole pack — everything joins your
          personal pool.
        </p>

        <div className="mt-7">
          <AddWordForm
            token={token}
            onPoolChanged={onPoolChanged}
            onUnauthorized={onUnauthorized}
          />

          <div className="mt-8 border-t-[3px] border-ink pt-6">
            <h3 className="text-center font-display text-[18px] font-black leading-tight text-ink">
              Or add a whole pack
            </h3>
            <div className="mt-5">
              <PackGallery
                token={token}
                onPoolChanged={onPoolChanged}
                onPractice={canPractice ? onClose : null}
                onUnauthorized={onUnauthorized}
              />
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
