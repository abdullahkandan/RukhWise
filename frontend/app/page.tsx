import { Section } from "@/components/Section";
import { Footer } from "@/components/Footer";
import { Hero } from "@/components/sections/Hero";
import { FlowField } from "@/components/FlowField";
import { InsightStrip } from "@/components/sections/InsightStrip";
import { DoorwayPanel } from "@/components/DoorwayPanel";
import { DataUnavailable } from "@/components/DataUnavailable";
import { isResearcherInsight, isMarketSkill } from "@/lib/insights";
import { getInsightsLive, getSkillsTop, getStatsOverview, getSystemHealth } from "@/lib/api";

export default async function HomePage() {
  const [stats, skillsAll, health, insights] = await Promise.all([
    getStatsOverview(),
    getSkillsTop({ includeSoft: true, limit: 100 }),
    getSystemHealth(),
    getInsightsLive(),
  ]);

  if (!stats || !skillsAll || !health) {
    return (
      <main>
        <Section register="cream">
          <div className="py-24 md:py-32">
            <DataUnavailable message="The live market feed is temporarily unavailable." />
          </div>
        </Section>
        <Footer />
      </main>
    );
  }

  // Home is the teaser -- the researcher-flavored findings (templated-share,
  // foreign-currency) live on /engine's "Findings for the curious" instead.
  // insights degrades gracefully (empty strip) rather than failing the
  // whole page -- it's a nice-to-have here, not load-bearing like Hero's
  // numbers above. Capped at 3, not a fixed count: fewer genuinely
  // noteworthy findings is better than padding out to a target number
  // with a weak one.
  const generalInsights = insights ? insights.insights.filter((i) => !isResearcherInsight(i)).slice(0, 3) : [];

  // Market-skill filter (requirement_type === 'skill', category not soft/
  // office_admin) -- see isMarketSkill's own comment. Without the
  // requirement_type half of this, the "market leader" teaser below
  // surfaces a work_arrangement attribute (On-Site) as if it were a skill.
  const marketSkills = skillsAll.skills.filter(isMarketSkill);
  const topSkill = marketSkills[0];

  return (
    <main>
      <Section register="cream" backdrop={<FlowField density="hero" />}>
        <Hero
          totalPostings={stats.total_postings}
          skillsMonitored={skillsAll.count}
          hoursSinceCollection={health.data_freshness_hours ? Math.round(health.data_freshness_hours) : 0}
        />
      </Section>

      {generalInsights.length > 0 && (
        <Section register="java">
          <InsightStrip insights={generalInsights} />
        </Section>
      )}

      <Section register="cream">
        <div className="py-24 md:py-32">
          <DoorwayPanel
            index={0}
            eyebrow="Analyzer"
            title="What should you learn next?"
            description="Tell it what you already know. It tells you, honestly, how many jobs you're a strong candidate for right now — and exactly what to learn for the biggest return."
            teaserValue={stats.total_postings}
            teaserLabel="live postings to match against"
            href="/analyzer"
          />
          <DoorwayPanel
            index={1}
            eyebrow="The Market"
            title="What employers ask for, where, and for how much"
            description="Skill demand ranked two ways, what goes together, and where the money is — by city and by experience level."
            teaserValue={topSkill ? topSkill.posting_count : 0}
            teaserLabel={topSkill ? `postings for ${topSkill.display}, the market leader` : "top tracked skill"}
            href="/market"
          />
          <DoorwayPanel
            index={2}
            eyebrow="Companies"
            title="Who is hiring"
            description="Ranked by posting volume, with the concentration made explicit — a single employer can hold a striking share of the corpus."
            teaserValue={Math.round(stats.top_company_share * 100)}
            teaserSuffix="%"
            teaserLabel={`of postings from one employer, of ${stats.distinct_companies} tracked`}
            href="/companies"
          />
        </div>
      </Section>

      <Footer />
    </main>
  );
}
