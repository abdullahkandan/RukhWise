import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { Footer } from "@/components/Footer";
import { MarketSkillsPanel } from "@/components/MarketSkillsPanel";
import { SkillCompare } from "@/components/SkillCompare";
import { GeographyMoney } from "@/components/sections/GeographyMoney";
import { ScrollReveal } from "@/components/ScrollReveal";
import {
  getSkillCompanions,
  getSkillsCompare,
  getSkillsCooccurrence,
  getSkillsTop,
  getStatsOverview,
} from "@/lib/api";

export const metadata: Metadata = {
  title: "The Market — Rukhwise",
  description: "What employers ask for, where, and for how much.",
};

export default async function MarketPage() {
  const [stats, skillsAll, cooccurrence] = await Promise.all([
    getStatsOverview(),
    getSkillsTop({ includeSoft: true, limit: 100 }),
    getSkillsCooccurrence({ limit: 5 }),
  ]);

  const technical = skillsAll.skills.filter((s) => s.category !== "soft");
  const defaultBundleSkill = technical.find((s) => s.skill === "sql")?.skill ?? technical[0]?.skill ?? "sql";
  const defaultA = defaultBundleSkill;
  const defaultB = technical.find((s) => s.skill === "python")?.skill ?? technical[1]?.skill ?? "python";

  const [companions, compareData] = await Promise.all([
    getSkillCompanions(defaultBundleSkill),
    getSkillsCompare(defaultA, defaultB),
  ]);

  return (
    <main>
      <Section register="java" id="skills">
        <div className="py-24 md:py-32">
          <ScrollReveal>
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-cream/50">The market</p>
            <h1 className="mt-3 max-w-2xl font-display text-4xl font-medium leading-tight md:text-5xl">
              What employers ask for, where, and for how much
            </h1>
          </ScrollReveal>

          <div className="mt-16">
            <MarketSkillsPanel
              initialSkills={skillsAll.skills}
              initialTotalPostings={skillsAll.total_postings}
              topCompanyShare={stats.top_company_share}
              initialCooccurrence={cooccurrence.pairs}
              defaultBundleSkill={defaultBundleSkill}
              initialCompanions={companions}
            />
          </div>
        </div>
      </Section>

      <Section register="cream" id="compare">
        <div className="py-24 md:py-32">
          <ScrollReveal>
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">Compare</p>
            <h2 className="mt-3 max-w-2xl font-display text-3xl font-medium leading-tight md:text-4xl">
              Compare two skills over time
            </h2>
          </ScrollReveal>
          <div className="mt-10">
            <SkillCompare
              skills={technical}
              initialData={compareData}
              initialA={defaultA}
              initialB={defaultB}
            />
          </div>
        </div>
      </Section>

      <Section register="java" id="geography">
        <GeographyMoney skills={skillsAll.skills} />
      </Section>

      <Footer />
    </main>
  );
}
