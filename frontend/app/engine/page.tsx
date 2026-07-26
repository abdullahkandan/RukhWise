import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { Footer } from "@/components/Footer";
import { EngineRoom } from "@/components/sections/EngineRoom";
import { InsightStrip } from "@/components/sections/InsightStrip";
import { isSystemInsight } from "@/lib/insights";
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

  // Both degrade gracefully rather than failing the page -- EngineRoom
  // below (system health, forecasts, backtest) is the load-bearing content
  // here; "Findings for the curious" is a nice-to-have that simply doesn't
  // render if either fetch comes back null.
  const curiousInsights = insights ? insights.insights.filter(isSystemInsight) : [];

  return (
    <main>
      <Section register="cream">
        <EngineRoom />
      </Section>

      {curiousInsights.length > 0 && (
        <Section register="java">
          <InsightStrip
            insights={curiousInsights}
            foreignCurrencyBreakout={foreignCurrency?.breakout_stack}
            eyebrow="Findings for the curious"
            heading="What the data reveals about itself"
          />
        </Section>
      )}

      <Footer />
    </main>
  );
}
