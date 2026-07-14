import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { Footer } from "@/components/Footer";
import { SkillGapAnalyzerSection } from "@/components/sections/SkillGapAnalyzer";
import { getSkillsTop } from "@/lib/api";

export const metadata: Metadata = {
  title: "Analyzer — Rukhwise",
  description: "What should you learn next? Find out how many jobs you're a strong match for.",
};

export default async function AnalyzerPage() {
  const skillsAll = await getSkillsTop({ includeSoft: true, limit: 100 });

  return (
    <main>
      <Section register="cream">
        <SkillGapAnalyzerSection skills={skillsAll.skills} />
      </Section>
      <Footer />
    </main>
  );
}
