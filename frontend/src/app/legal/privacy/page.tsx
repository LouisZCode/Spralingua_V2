import type { Metadata } from "next";
import {
  LegalTitle,
  LegalMeta,
  LegalH2,
  LegalH3,
  LegalP,
  LegalLines,
  LegalUL,
  LegalTable,
  B,
} from "../prose";

export const metadata: Metadata = {
  title: "Privacy Policy — Spralingua",
};

export default function PrivacyPage() {
  return (
    <>
      <LegalTitle>Privacy Policy (Datenschutzerklärung) for spralingua.com</LegalTitle>
      <LegalMeta>Last updated: 5 September 2026</LegalMeta>

      <LegalP>
        This Privacy Policy explains what personal data spralingua.com
        (&quot;Spralingua,&quot; &quot;we,&quot; &quot;us&quot;) collects
        when you use our website and app, why we collect it, who we share it
        with, and what rights you have. Spralingua teaches German through
        real-time voice conversations with AI conversation partners and
        AI-graded written and spoken exercises. We are based in Germany, and
        this policy is written to comply with the EU General Data Protection
        Regulation (GDPR).
      </LegalP>
      <LegalH2>1. Who is responsible for your data (the controller)</LegalH2>
      <LegalP>
        The controller responsible for the personal data described in this
        policy is:
      </LegalP>
      <LegalLines lines={["Spralingua", "Berlin, Germany"]} />
      <LegalP>
        Spralingua is operated as a sole proprietorship. Given the current
        size and nature of our processing, we are not required to appoint a
        Data Protection Officer under Art. 37 GDPR, and we have not appointed
        one. For any privacy question or request, please contact us via the
        contact details published in our Impressum.
      </LegalP>

      <LegalH2>2. What personal data we collect</LegalH2>
      <LegalP>
        We collect only the data needed to run the service. Here is exactly
        what that is.
      </LegalP>

      <LegalH3>2.1 Account and sign-in data</LegalH3>
      <LegalP>
        You sign in with Google. We receive and store your Google account ID
        (the &quot;sub&quot; claim), your email address, your name, and your
        profile picture URL. We check whether Google has verified your email
        address at sign-in, but we do not store that verification flag. This
        data is stored together with your account, streak, and level data.
      </LegalP>

      <LegalH3>2.2 Voice conversations: recordings and transcripts</LegalH3>
      <LegalP>
        When you have a voice conversation with an AI partner, we record and
        keep the <B>full audio of that session as an MP3 file</B>. This
        applies to every voice conversation on Spralingua, including:
      </LegalP>
      <LegalUL>
        <li>
          the unauthenticated homepage demo, which is recorded under a
          shared, anonymous &quot;demo&quot; identity and capped at roughly
          three minutes; and
        </li>
        <li>
          conversations with our AI teacher persona (&quot;Clara&quot;), who
          explains German grammar in English.
        </li>
      </LegalUL>
      <LegalP>
        We also generate and store a <B>full text transcript</B> of every
        voice session in our database.
      </LegalP>

      <LegalH3>2.3 Written and spoken exercises and your personal error record</LegalH3>
      <LegalP>
        When you complete a written or spoken exercise, your answer is
        processed by an AI system to grade it. We store the graded outcome
        (pass/fail and the grammar pattern involved).
      </LegalP>
      <LegalP>
        To personalize your practice, we also keep a{" "}
        <B>learning-error record</B>: for each grammar pattern you tend to
        get wrong, we store{" "}
        <B>up to five verbatim example sentences of your own mistakes</B>,
        together with the corrected version. In plain terms:{" "}
        <B>
          we store examples of your actual mistakes so we can tailor future
          practice to you.
        </B>
      </LegalP>
      <LegalP>
        For spoken exercises specifically, we also keep the{" "}
        <B>audio recording of your attempt itself</B> — not just the graded
        outcome or a text example. Each recording is linked to your account
        and to the exercise it belongs to, and is used to improve how we
        grade attempts and to personalize your practice further. It is
        stored in the same EU-West cloud storage bucket as the voice-
        conversation recordings described in section 2.2.
      </LegalP>

      <LegalH3>2.4 Vocabulary, progress and streak data</LegalH3>
      <LegalP>
        We store your vocabulary deck and card progress, your drill history,
        your daily streak and daily-completion data, and the CEFR level you
        have told us you are at (self-declared, not independently verified).
      </LegalP>

      <LegalH3>2.5 What we store in your browser — and what we don&apos;t</LegalH3>
      <LegalP>
        We use your browser&apos;s <code>localStorage</code> (not cookies) to
        store two things: your session token and a local copy of your
        profile (together, <code>spralingua_auth</code>), and a small set of
        non-personal UI preferences (such as display settings).
      </LegalP>
      <LegalP>
        We do <B>not</B> use cookies. We do <B>not</B> use any analytics,
        tracking, or advertising technology of any kind — not our own, and
        not from any third party.
      </LegalP>
      <LegalP>
        Because the only thing stored locally is a login token that is
        strictly necessary to keep you signed in, this falls under the
        &quot;strictly necessary&quot; exemption in § 25(2) of the German
        Telecommunications-Digital-Services-Data-Protection Act (TDDDG). That
        is why you do not see a cookie/consent banner on Spralingua: nothing
        non-essential is being stored without your knowledge, and there is
        nothing non-essential to ask consent for.
      </LegalP>

      <LegalH3>2.6 IP addresses</LegalH3>
      <LegalP>
        Our application itself does not store your IP address. It is used
        only transiently, in memory, to apply rate limits (for example, to
        stop abuse of the free homepage demo), and is not written to any
        database or log we control. Our hosting provider, Railway,
        necessarily processes connection-level data (such as IP addresses) as
        part of operating the servers, in the same way any web host does.
      </LegalP>

      <LegalH2>3. Why we process your data, and our legal basis</LegalH2>
      <LegalP>
        GDPR requires us to have a specific legal basis for each purpose we
        process data for. We do not rely on one blanket basis — here is the
        basis for each activity:
      </LegalP>
      <LegalTable
        headers={["What we do", "Legal basis"]}
        rows={[
          [
            "Creating and maintaining your account, signing you in",
            "Art. 6(1)(b) GDPR — necessary to perform our contract with you",
          ],
          [
            "Running the voice conversation service (speech-to-text, generating the AI's reply, speech synthesis)",
            "Art. 6(1)(b) GDPR — necessary to provide the service you signed up for",
          ],
          [
            "AI grading of exercises, maintaining your personal error ledger, and personalizing your practice",
            "Art. 6(1)(b) GDPR — this personalization is the core of what you sign up for",
          ],
          [
            "Storing session recordings and transcripts, so you can review your own history and so we can maintain service quality",
            "Art. 6(1)(b) and Art. 6(1)(f) GDPR — contract performance, and our legitimate interest in maintaining and improving service quality",
          ],
          [
            "Observability and tracing of our systems (via Langfuse), for reliability and cost monitoring",
            "Art. 6(1)(f) GDPR — our legitimate interest in keeping the service reliable and financially sustainable. Note: these technical traces include the content of your conversations and the prompts used to generate replies",
          ],
        ]}
      />
      <LegalP>
        <B>What happens if you don&apos;t provide this data:</B> an email
        address and a Google account are required to create a Spralingua
        account. Without one, you can still use the free, unauthenticated
        homepage demo, but you cannot access the full service (saved
        progress, personalized practice, tandem partners, and so on).
      </LegalP>

      <LegalH2>4. Who else sees your data</LegalH2>
      <LegalP>
        We use a small number of outside service providers
        (&quot;processors&quot;) to run Spralingua. None of them are allowed
        to use your data for their own purposes. The table below lists each
        one, what it receives, and the legal mechanism that allows us to send
        data there if it leaves the EU/EEA.
      </LegalP>
      <LegalTable
        headers={[
          "Service",
          "Company",
          "Country",
          "What it receives",
          "Transfer mechanism",
        ]}
        rows={[
          [
            "Speech-to-text",
            "Deepgram Inc.",
            "United States",
            "Your voice audio — both live conversation streams and recorded spoken-exercise clips — for transcription",
            "EU Standard Contractual Clauses (SCCs)",
          ],
          [
            "Text-to-speech (AI tutor's voice)",
            "MiniMax",
            "China",
            <>
              <B>Only the text of the AI tutor&apos;s own reply</B>, so it
              can be spoken aloud. MiniMax never receives your voice audio,
              anything you wrote or said, or any identifier that could
              identify you.
            </>,
            "EU Standard Contractual Clauses (SCCs)",
          ],
          [
            "AI conversation and grading (primary)",
            "Cerebras Systems Inc.",
            "United States",
            "The text of your conversation and your exercise answers, to generate the AI's replies and to grade your work",
            "EU Standard Contractual Clauses (SCCs)",
          ],
          [
            "AI conversation and grading (routing / fallback)",
            "OpenRouter Inc.",
            "United States",
            "The same conversation/exercise text, used as a routing layer and as a fallback if our primary provider is unavailable",
            "EU Standard Contractual Clauses (SCCs)",
          ],
          [
            "Observability and tracing",
            "Langfuse",
            "European Union (cloud.langfuse.com is EU-hosted)",
            "Conversation content, exercise data and technical performance data, to monitor reliability and cost",
            "Not applicable — this data stays within the EU",
          ],
          [
            "Sign-in",
            "Google LLC",
            "United States",
            "Your Google account ID, email, name and profile picture, to authenticate you",
            "EU-U.S. Data Privacy Framework",
          ],
          [
            "Pronunciation assessment (when active)",
            "Microsoft Corporation (Azure Speech)",
            "Processed in the EU (West Europe/Amsterdam region); Microsoft is a US-headquartered company",
            <>
              Your voice audio, to assess your pronunciation,{" "}
              <B>when this feature is active</B>. This feature is not
              currently active, but remains built into the service and may
              be re-enabled.
            </>,
            "Processing occurs within the EU; EU-U.S. Data Privacy Framework applies to any incidental US-based administrative or support access",
          ],
          [
            "Hosting (servers, database, file storage)",
            "Railway Corporation",
            "Servers, database and audio files all run in Railway's EU-West (Amsterdam) region; Railway is a US-headquartered company",
            "All application data at rest — your account, database records and audio recordings — plus ordinary connection/hosting data",
            "Application data at rest is stored in the EU; EU-U.S. Data Privacy Framework applies to any US-based administrative or support access",
          ],
        ]}
      />
      <LegalP>
        <B>
          In plain terms: all of your application data at rest — your
          account, your database records, your audio recordings — is stored
          on servers in the EU.
        </B>
      </LegalP>
      <LegalP>
        We do not currently process any payment data. Spralingua is free
        today; paid plans are planned. Before any paid plan launches, this
        policy will be updated to name our payment processor and describe
        that processing.
      </LegalP>

      <LegalH2>5. How long we keep your data</LegalH2>
      <LegalUL>
        <li>
          <B>Account and learning data</B> (profile, vocabulary deck, drill
          history, streak, error ledger): kept for as long as your account
          exists, and deleted when you ask us to delete your account.
        </li>
        <li>
          <B>Voice recordings</B> (session audio and spoken-exercise clips,
          see sections 2.2 and 2.3): stored in our EU-West storage bucket
          for as long as your account exists, and removed when you ask us
          to delete your account. We have not yet set a shorter fixed
          retention period for audio; when we do, this policy will be
          updated first.
        </li>
        <li>
          <B>Transcripts and other learning records</B>: kept for as long as
          your account exists.
        </li>
        <li>
          <B>Observability traces</B> (Langfuse): kept according to our
          observability provider&apos;s project-level retention settings.
        </li>
      </LegalUL>

      <LegalH2>6. Your rights</LegalH2>
      <LegalP>Under the GDPR, you have the right to:</LegalP>
      <LegalUL>
        <li>
          <B>Access</B> the personal data we hold about you
        </li>
        <li>
          <B>Rectify</B> inaccurate data
        </li>
        <li>
          <B>Erase</B> your data (&quot;right to be forgotten&quot;)
        </li>
        <li>
          <B>Restrict</B> how we process your data
        </li>
        <li>
          <B>Object</B> to processing based on our legitimate interest (Art.
          6(1)(f))
        </li>
        <li>
          <B>Data portability</B> — receive your data in a structured,
          machine-readable format
        </li>
        <li>
          <B>Withdraw consent</B> at any time, for any processing that is
          based on consent. Note: none of the processing described in this
          policy is currently based on your consent (Art. 6(1)(a)) — see
          Section 3 for the actual bases we rely on.
        </li>
      </LegalUL>
      <LegalP>
        <B>How to exercise these rights:</B> Spralingua does not yet have a
        self-service way to export or delete your data in the app. To
        exercise any of the rights above, please contact us via the contact
        details published in our Impressum. We will respond within one
        month, as required by Art. 12(3)
        GDPR (this can be extended by two further months for complex
        requests, in which case we will tell you why).
      </LegalP>
      <LegalP>
        <B>Right to complain:</B> You also have the right to lodge a
        complaint with a data protection supervisory authority. You may
        complain to any supervisory authority in the EU, including the
        authority for the German federal state (Land) where we are
        established.
      </LegalP>

      <LegalH2>7. Automated evaluation of your practice</LegalH2>
      <LegalP>
        Your written and spoken exercises, and your voice conversations, are
        evaluated automatically by AI systems — for example, to grade an
        answer, judge pronunciation, or detect a grammar error. These
        automated evaluations only affect{" "}
        <B>practice recommendations and in-app feedback</B> — for example,
        which grammar patterns we focus on with you next, or what your AI
        tutor brings up in conversation. They do not produce any legal
        effect or similarly significant effect on you, so they do not fall
        under Art. 22 GDPR.
      </LegalP>

      <LegalH2>8. AI transparency — you are always talking to an AI</LegalH2>
      <LegalP>
        In line with Art. 50 of the EU AI Act, we tell you plainly:{" "}
        <B>
          every conversation partner and tutor on Spralingua is an AI
          system, and all of their speech is synthetically generated
        </B>{" "}
        — none of it is a real person. You are informed of this in-product
        before every session, not just here.
      </LegalP>

      <LegalH2>9. Age requirement</LegalH2>
      <LegalP>
        Spralingua is intended for users aged <B>16 or over</B>, consistent
        with the age of consent for information-society services under Art.
        8 GDPR and German law. We do not knowingly collect data from users
        under 16.
      </LegalP>

      <LegalH2>10. Changes to this policy</LegalH2>
      <LegalP>
        We may update this policy as the service changes — for example, when
        we introduce paid plans, or if we re-enable pronunciation feedback.
        We will update the &quot;Last updated&quot; date at the top when we
        do.
      </LegalP>
      <LegalP>
        We do not use analytics, tracking, or advertising today.{" "}
        <B>
          If that ever changes, we will update this policy and put a proper
          consent mechanism in place first
        </B>
        , before any such technology goes live — not after.
      </LegalP>
    </>
  );
}
