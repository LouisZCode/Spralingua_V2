import type { Metadata } from "next";
import { LegalTitle, LegalH2, LegalP, LegalLines } from "../prose";

export const metadata: Metadata = {
  title: "Impressum — Spralingua",
};

export default function ImpressumPage() {
  return (
    <>
      <LegalTitle>Impressum (Legal Notice)</LegalTitle>
      <LegalP>Information pursuant to § 5 DDG (Digitale-Dienste-Gesetz).</LegalP>

      <LegalH2>Operator</LegalH2>
      <LegalLines lines={["Spralingua", "Berlin, Germany"]} />

      <LegalH2>Responsible for content</LegalH2>
      <LegalP>Pursuant to § 18 Abs. 2 MStV:</LegalP>
      <LegalLines lines={["Spralingua", "Berlin, Germany"]} />

      <LegalH2>Consumer dispute resolution</LegalH2>
      <LegalP>
        We are not willing and not obliged to participate in dispute
        resolution proceedings before a consumer arbitration board
        (Verbraucherschlichtungsstelle) pursuant to § 36 VSBG.
      </LegalP>
    </>
  );
}
