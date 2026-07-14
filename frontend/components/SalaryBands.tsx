import Link from "next/link";
import type { SalariesSummaryResponse } from "@/lib/api";
import { formatPKR } from "@/lib/format";
import { CountUp } from "./CountUp";

interface SalaryBandsProps {
  data: SalariesSummaryResponse;
  foreignCurrencyCount: number;
  skillLabel?: string;
  isPending?: boolean;
}

const BAND_ORDER = [
  "Entry (0-1 yrs)",
  "Junior (2-3 yrs)",
  "Mid (4-6 yrs)",
  "Senior (7+ yrs)",
  "Unspecified",
];

// Below this many salary-disclosed postings, a median is noise, not signal.
const MIN_RELIABLE = 8;

export function SalaryBands({ data, foreignCurrencyCount, skillLabel, isPending = false }: SalaryBandsProps) {
  const bands = BAND_ORDER.map((label) => ({ label, stats: data.by_experience_band[label] })).filter(
    (b) => b.stats && b.stats.count > 0
  );

  const allValues = bands.flatMap((b) => [b.stats.q1 ?? 0, b.stats.q3 ?? 0]);
  const scaleMax = Math.max(...allValues, data.overall.q3 ?? 0, 1);

  const withSalary = data.overall.postings_with_salary ?? 0;
  const hasSkill = Boolean(data.skill && skillLabel);
  const lowData = hasSkill && withSalary < MIN_RELIABLE;

  return (
    <div className={`transition-opacity duration-200 ${isPending ? "opacity-50" : "opacity-100"}`}>
      <h3 className="font-display text-2xl font-medium leading-tight md:text-3xl">
        What it pays, by experience
      </h3>
      <p className="mt-2 font-mono text-xs text-cream/50">
        {hasSkill
          ? `For ${skillLabel}: ${withSalary} ${withSalary === 1 ? "posting" : "postings"} with salary data.`
          : "All tracked postings, market-wide."}
      </p>

      {lowData ? (
        <div className="mt-10 border border-cream/15 px-6 py-10">
          <p className="max-w-sm font-sans text-sm leading-relaxed text-cream/70">
            Too few salary-disclosed postings for a reliable median. As collection
            continues, {skillLabel} may cross the threshold.
          </p>
        </div>
      ) : (
        <>
          <p className="mt-2 max-w-lg font-sans text-sm text-cream/60">
            Medians, because a single outlier posting can make averages lie.
          </p>

          <div className="mt-10 flex flex-col gap-6">
            {bands.map(({ label, stats }) => {
              const q1 = stats.q1 ?? 0;
              const q3 = stats.q3 ?? 0;
              const median = stats.median ?? 0;
              const leftPct = (q1 / scaleMax) * 100;
              const widthPct = Math.max(1, ((q3 - q1) / scaleMax) * 100);
              const medianPct = (median / scaleMax) * 100;

              return (
                <div key={label}>
                  <div className="flex items-baseline justify-between">
                    <span className="font-sans text-sm font-medium">{label}</span>
                    <CountUp
                      value={median}
                      formatter={formatPKR}
                      className="font-mono tabular-nums text-lg text-cerulean"
                    />
                  </div>
                  <div className="relative mt-2 h-2 bg-cream/10">
                    <div
                      className="absolute h-full bg-cerulean/40"
                      style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                    />
                    <div
                      className="absolute top-1/2 h-3 w-[3px] -translate-y-1/2 bg-cerulean"
                      style={{ left: `${medianPct}%` }}
                    />
                  </div>
                  <div className="mt-1 flex justify-between font-mono text-[11px] text-cream/40">
                    <span>Q1 {formatPKR(q1)}</span>
                    <span>Q3 {formatPKR(q3)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      <p className="mt-8 font-mono text-xs text-cream/50">
        {foreignCurrencyCount} postings priced outside PKR, excluded from these figures. See{" "}
        <Link href="/methodology" className="underline underline-offset-2 hover:text-cream">
          methodology
        </Link>
        .
      </p>
    </div>
  );
}
