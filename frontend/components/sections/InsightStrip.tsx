import Link from "next/link";
import type { ForeignCurrencyBreakoutEntry, Insight } from "@/lib/api";
import { classifyInsight } from "@/lib/insights";
import { ScrollReveal } from "../ScrollReveal";

interface InsightStripProps {
  insights: Insight[];
  foreignCurrencyBreakout?: ForeignCurrencyBreakoutEntry[];
  eyebrow?: string;
  heading?: string;
}

function isForeignCurrencyInsight(insight: Insight): boolean {
  return typeof insight.value.by_currency === "object" && insight.value.by_currency !== null;
}

function InsightCard({
  insight,
  index,
  foreignCurrencyBreakout,
}: {
  insight: Insight;
  index: number;
  foreignCurrencyBreakout?: ForeignCurrencyBreakoutEntry[];
}) {
  const { tone, bigNumber } = classifyInsight(insight);
  const numberColor = tone === "alert" ? "text-sceptre-bright" : "text-cerulean";
  const showBreakout = isForeignCurrencyInsight(insight) && (foreignCurrencyBreakout?.length ?? 0) > 0;

  return (
    <ScrollReveal index={index} staggerMs={80} className="h-full">
      <article className="flex h-full flex-col justify-between gap-6 border border-cream/15 bg-soil px-6 py-7 transition-colors duration-150 hover:border-cream/35">
        <h3 className="font-display text-xl font-medium leading-snug text-balance">
          {insight.headline}
        </h3>
        {bigNumber && (
          <p className={`font-mono tabular-nums text-5xl font-semibold ${numberColor}`}>
            {bigNumber}
          </p>
        )}
        <div>
          <p className="font-sans text-sm leading-relaxed text-cream/70">
            {insight.detail}
          </p>
          {showBreakout && (
            <p className="mt-2 font-mono text-xs text-cream/50">
              Skill stack: {foreignCurrencyBreakout!.slice(0, 3).map((s) => s.display).join(", ")}.{" "}
              <Link href="/market#geography" className="underline underline-offset-2 hover:text-cream">
                See breakdown
              </Link>
            </p>
          )}
        </div>
      </article>
    </ScrollReveal>
  );
}

export function InsightStrip({
  insights,
  foreignCurrencyBreakout,
  eyebrow = "Findings, computed live",
  heading = "What the data says this week",
}: InsightStripProps) {
  if (insights.length === 0) return null;

  return (
    <div className="py-24 md:py-32">
      <ScrollReveal>
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-cream/50">{eyebrow}</p>
        <h2 className="mt-3 max-w-2xl font-display text-3xl font-medium leading-tight md:text-4xl">
          {heading}
        </h2>
      </ScrollReveal>

      <div className="mt-12 grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-4">
        {insights.map((insight, i) => (
          <InsightCard
            key={insight.headline}
            insight={insight}
            index={i}
            foreignCurrencyBreakout={foreignCurrencyBreakout}
          />
        ))}
      </div>
    </div>
  );
}
