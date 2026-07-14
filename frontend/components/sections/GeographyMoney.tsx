import { GeographyPanels } from "../GeographyPanels";
import { ScrollReveal } from "../ScrollReveal";
import { getCitiesBreakdown, getInsightsLive, getSalariesSummary, type SkillTopEntry } from "@/lib/api";

interface GeographyMoneyProps {
  skills: SkillTopEntry[];
}

export async function GeographyMoney({ skills }: GeographyMoneyProps) {
  const [cities, salaries, insights] = await Promise.all([
    getCitiesBreakdown(),
    getSalariesSummary("PKR"),
    getInsightsLive(),
  ]);

  const foreignCurrencyInsight = insights.insights.find(
    (i) => typeof i.value.by_currency === "object" && i.value.by_currency !== null
  );
  const foreignCurrencyCount = (foreignCurrencyInsight?.value.count as number) ?? 0;

  const technical = skills.filter((s) => s.category !== "soft");

  return (
    <div className="py-24 md:py-32">
      <ScrollReveal>
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-cream/50">
          Geography &amp; money
        </p>
        <h2 className="mt-3 max-w-2xl font-display text-3xl font-medium leading-tight md:text-4xl">
          Where, and for how much
        </h2>
      </ScrollReveal>

      <GeographyPanels
        skills={technical}
        initialCities={cities}
        initialSalaries={salaries}
        foreignCurrencyCount={foreignCurrencyCount}
      />
    </div>
  );
}
