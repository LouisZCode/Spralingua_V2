import type { Metadata } from "next";
import TeacherChat from "@/components/TeacherChat";

export const metadata: Metadata = {
  title: "Spralingua — Your German Teacher",
};

export default function TeacherPage() {
  return <TeacherChat />;
}
