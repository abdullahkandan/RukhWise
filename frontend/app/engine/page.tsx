import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { Footer } from "@/components/Footer";
import { EngineRoom } from "@/components/sections/EngineRoom";
import { InsightStrip } from "@/components/sections/InsightStrip";
import { isResearcherInsight } from "@/lib/insights";
import { getInsightsLive, getPostingsForeignCurrency } from "@/lib/api";

export const metadata: Metadata = {
  title: "The Engine Room — Rukhwise",
  description: "System health, forecasts, and the findings for the curious.",
};

export default async function EnginePage() {
  const [insights, foreignCurrency] = await Promise.all([
    getInsightsLive(),
    getPostingsForeignCurrency(),
  ]);

  const curiousInsights = insights.insights.filter(isResearcherInsight);

  return (
    <main>
      <Section register="cream">
        <EngineRoom />
      </Section>

      {curiousInsights.length > 0 && (
        <Section register="java">
          <InsightStrip
            insights={curiousInsights}
            foreignCurrencyBreakout={foreignCurrency.breakout_stack}
            eyebrow="Findings for the curious"
            heading="What the data reveals about itself"
          />
        </Section>
      )}

      <Footer />
    </main>
  );
}
