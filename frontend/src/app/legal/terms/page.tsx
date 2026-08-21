import type { Metadata } from "next";
import {
  LegalTitle,
  LegalMeta,
  LegalH2,
  LegalH3,
  LegalP,
  LegalLines,
  LegalUL,
} from "../prose";

export const metadata: Metadata = {
  title: "Terms of Service — Spralingua",
};

export default function TermsPage() {
  return (
    <>
      <LegalTitle>Terms of Service</LegalTitle>
      <LegalMeta>Last updated: 21 August 2026</LegalMeta>

      <LegalH2>1. Who we are and what these terms cover</LegalH2>
      <LegalP>
        These Terms of Service (&quot;Terms&quot;) govern your use of
        spralingua.com and the Spralingua application (the &quot;Service&quot;).
        The Service is provided by:
      </LegalP>
      <LegalLines lines={["Spralingua", "Berlin, Germany"]} />
      <LegalP>
        (&quot;we&quot;, &quot;us&quot;, &quot;the operator&quot;). Further
        details about the provider, including the information required under
        German law, are available in our Impressum.
      </LegalP>
      <LegalP>
        By creating an account or using the Service, you agree to these
        Terms. If you do not agree, please do not use the Service.
      </LegalP>

      <LegalH2>2. The service</LegalH2>
      <LegalP>
        Spralingua is an AI-powered application for learning German. It
        offers real-time voice conversations with AI conversation partners
        and an AI tutor, AI-graded written and spoken exercises, and
        personalized practice based on your progress and mistakes.
      </LegalP>

      <LegalH3>You are talking to AI</LegalH3>
      <LegalP>
        Every conversation partner and tutor on Spralingua — including
        personas such as &quot;Lena,&quot; &quot;Paul,&quot; and
        &quot;Clara&quot; — is an artificial-intelligence system. Their
        voices are synthetically generated. No human is on the other side of
        any conversation, exercise, or piece of feedback on Spralingua.
      </LegalP>

      <LegalH2>3. AI-generated content — no guarantee of accuracy</LegalH2>
      <LegalP>
        The Service uses AI to hold conversations, grade exercises, and
        generate corrections, explanations, and example sentences.
        AI-generated output can be wrong. Corrections may miss errors, grades
        may be inaccurate, and explanations or example sentences may contain
        mistakes.
      </LegalP>
      <LegalP>
        Spralingua is a learning aid. It is not a substitute for professional
        language instruction, a certified language assessment, or a
        recognized proficiency exam (e.g. Goethe-Institut, telc). We do not
        guarantee any particular learning outcome, progress rate, or
        proficiency level from using the Service.
      </LegalP>

      <LegalH2>4. Accounts</LegalH2>
      <LegalP>
        Full access to the Service requires signing in with a Google account.
        A limited demo on the homepage is available without an account.
      </LegalP>
      <LegalP>
        When you create an account, you must provide accurate information and
        keep your login credentials confidential. You are responsible for
        activity that occurs under your account. Notify us via the contact
        details in our Impressum if you suspect unauthorized use of your
        account.
      </LegalP>

      <LegalH2>5. Age requirement</LegalH2>
      <LegalP>
        You must be at least 16 years old to create an account. By creating
        an account, you confirm that you meet this requirement.
      </LegalP>

      <LegalH2>6. Free beta</LegalH2>
      <LegalP>
        The Service is currently provided free of charge, as a beta.
        Features may be added, changed, limited, interrupted, or discontinued
        at any time, and we do not guarantee any particular level of
        availability or uptime.
      </LegalP>
      <LegalP>
        If we introduce paid plans in the future, they will be governed by
        updated terms communicated to users in advance.
      </LegalP>

      <LegalH2>7. Acceptable use</LegalH2>
      <LegalP>When using the Service, you must not:</LegalP>
      <LegalUL>
        <li>use it for any unlawful purpose or in violation of applicable law;</li>
        <li>
          attempt to disrupt, overload, or impair the Service, or circumvent
          any security or access controls;
        </li>
        <li>
          reverse-engineer, decompile, or attempt to extract the underlying
          models, prompts, or source code of the Service;
        </li>
        <li>direct abusive, harassing, or illegal content at the Service or its AI systems;</li>
        <li>
          scrape, crawl, or access the Service through automated means other
          than the intended user interface; or
        </li>
        <li>
          submit content that infringes the intellectual property, privacy,
          or other rights of a third party.
        </li>
      </LegalUL>
      <LegalP>
        We may suspend or restrict access for conduct that violates this
        section.
      </LegalP>

      <LegalH2>8. Your content and the license you grant us</LegalH2>
      <LegalP>
        You retain all rights to the content you create when using the
        Service, including your spoken and written submissions, exercise
        answers, and any other input you provide (&quot;User Content&quot;).
      </LegalP>
      <LegalP>
        By using the Service, you grant us a license to process your User
        Content as needed to provide and improve the Service — including
        transcription, AI grading and feedback, tracking your errors and
        progress, and quality assurance — as described in our Privacy Policy.
      </LegalP>

      <LegalH2>9. Intellectual property</LegalH2>
      <LegalP>
        The Service, including its software, exercises, lesson content, AI
        personas, and branding (including the Spralingua name and logo),
        belongs to us or our licensors. We grant you a personal,
        non-exclusive, non-transferable right to use the Service for your
        own, non-commercial language learning. You may not copy, redistribute,
        or commercially exploit the Service or its content without our prior
        written consent.
      </LegalP>

      <LegalH2>10. Liability</LegalH2>
      <LegalP>
        We are liable without limitation for damages caused by intent or
        gross negligence, and for damages resulting from injury to life,
        body, or health.
      </LegalP>
      <LegalP>
        For damages caused by slight (ordinary) negligence, we are liable
        only for breach of an essential contractual obligation
        (<em>Kardinalpflicht</em>) — an obligation whose fulfillment is
        necessary to achieve the purpose of these Terms and on which you may
        reasonably rely. In such cases, our liability is limited to the
        foreseeable damage typical for a service of this kind.
      </LegalP>
      <LegalP>
        Liability under mandatory statutory provisions, including the German
        Product Liability Act (<em>Produkthaftungsgesetz</em>), remains
        unaffected.
      </LegalP>

      <LegalH2>11. Termination</LegalH2>
      <LegalP>
        You may stop using the Service at any time. You may request deletion
        of your account by contacting us via the contact details in our
        Impressum.
      </LegalP>
      <LegalP>
        We may suspend or terminate your account if you violate these Terms.
        We may also discontinue the free beta, in whole or in part, giving
        reasonable advance notice where circumstances allow.
      </LegalP>

      <LegalH2>12. Changes to these terms</LegalH2>
      <LegalP>
        We may update these Terms for reasonable cause, such as changes to
        the Service or applicable law. We will give you reasonable advance
        notice of any material change before it takes effect. If you continue
        using the Service after a change takes effect, that continued use
        constitutes acceptance of the updated Terms. If you do not agree to a
        change, you may stop using the Service and, where applicable, request
        account deletion.
      </LegalP>

      <LegalH2>13. Consumer dispute resolution</LegalH2>
      <LegalP>
        We are not obliged, and are not willing, to participate in dispute
        resolution proceedings before a consumer arbitration board
        (<em>Verbraucherschlichtungsstelle</em>) within the meaning of the
        German Consumer Dispute Resolution Act (VSBG § 36).
      </LegalP>

      <LegalH2>14. Final provisions</LegalH2>
      <LegalP>
        These Terms are governed by German law, without prejudice to any
        mandatory consumer protection provisions of the country in which
        you, as a consumer, have your habitual residence.
      </LegalP>
      <LegalP>
        If any provision of these Terms is or becomes invalid or
        unenforceable, the remaining provisions remain in full force. The
        invalid provision will be treated as replaced by a valid provision
        that most closely reflects its intended commercial purpose.
      </LegalP>
    </>
  );
}
