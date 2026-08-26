import type { Metadata } from "next";
import { Bricolage_Grotesque, Outfit, Geist_Mono } from "next/font/google";
import "./globals.css";
import Providers from "@/components/auth/Providers";

const bricolage = Bricolage_Grotesque({
  variable: "--font-display",
  subsets: ["latin"],
  display: "swap",
});

const outfit = Outfit({
  variable: "--font-body",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Spralingua",
  description: "Voice-powered language learning",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="scroll-smooth" suppressHydrationWarning>
      <head>
        {/* DARK-001: resolve the theme BEFORE first paint, or the page
            renders light and then snaps to dark. Runs synchronously in
            <head>, reads the learner's explicit choice first and falls back
            to the OS. globals.css keys its dark token set off this
            attribute, so stamping it here is the whole switch. Failing
            closed to "light" keeps today's experience when storage or
            matchMedia throws (Safari private mode). */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var s=localStorage.getItem('spralingua-theme-v1');var d=s==='dark'||(s!=='light'&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.setAttribute('data-theme',d?'dark':'light')}catch(e){document.documentElement.setAttribute('data-theme','light')}})()`,
          }}
        />
      </head>
      <body
        className={`${bricolage.variable} ${outfit.variable} ${geistMono.variable} antialiased`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
