"use client";

import Script from "next/script";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";
import { useConsent } from "@/lib/consent";

// Meta Pixel base code (Events Manager snippet), adapted for the App Router.
// NEXT_PUBLIC_ prefix so the browser bundle can read it — inlined at BUILD
// time, so a changed value needs a rebuild. The hardcoded fallback is the ID
// from the snippet itself, keeping tracking alive if the env var is unset.
const PIXEL_ID = process.env.NEXT_PUBLIC_META_PIXEL_ID || "1433402562081357";

type Fbq = (...args: unknown[]) => void;

declare global {
  interface Window {
    fbq?: Fbq;
  }
}

function PixelTracker() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const firstLoad = useRef(true);

  useEffect(() => {
    // The base snippet already sends a PageView on fbq('init'), so skip the
    // first run — otherwise every initial page load counts twice.
    if (firstLoad.current) {
      firstLoad.current = false;
      return;
    }
    window.fbq?.("track", "PageView");
  }, [pathname, searchParams]);

  return null;
}

export default function MetaPixel() {
  // LEGAL-003 (A1): the whole Pixel — base snippet, route-change tracker
  // and the noscript fallback — mounts only after consent is granted.
  // Consent arriving later (banner/notice/toggle) re-renders this and the
  // snippet's own init fires PageView; consent revoked unmounts it, and
  // lib/fbpixel.ts::trackFbq additionally guards every explicit event so
  // an already-loaded fbq can never fire post-revoke.
  const consent = useConsent();
  if (consent !== "granted") return null;

  return (
    <>
      <Script id="meta-pixel" strategy="afterInteractive">
        {`!function(f,b,e,v,n,t,s)
        {if(f.fbq)return;n=f.fbq=function(){n.callMethod?
        n.callMethod.apply(n,arguments):n.queue.push(arguments)};
        if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
        n.queue=[];t=b.createElement(e);t.async=!0;
        t.src=v;s=b.getElementsByTagName(e)[0];
        s.parentNode.insertBefore(t,s)}(window, document,'script',
        'https://connect.facebook.net/en_US/fbevents.js');
        fbq('init', '${PIXEL_ID}');
        fbq('track', 'PageView');`}
      </Script>
      {/* useSearchParams requires a Suspense boundary or `next build` fails
          on statically rendered routes. The tracker renders null. */}
      <Suspense fallback={null}>
        <PixelTracker />
      </Suspense>
      <noscript>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          height="1"
          width="1"
          style={{ display: "none" }}
          src={`https://www.facebook.com/tr?id=${PIXEL_ID}&ev=PageView&noscript=1`}
          alt=""
        />
      </noscript>
    </>
  );
}
