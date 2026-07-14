import { getCompaniesTop } from "@/lib/api";
import { formatPKR } from "@/lib/format";
import { ScrollReveal } from "../ScrollReveal";

interface CompaniesProps {
  totalPostings: number;
}

export async function Companies({ totalPostings }: CompaniesProps) {
  const { companies } = await getCompaniesTop({ limit: 10 });

  return (
    <div className="py-24 md:py-32">
      <ScrollReveal>
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-cream/50">
          Employer intelligence
        </p>
        <h2 className="mt-3 max-w-2xl font-display text-3xl font-medium leading-tight md:text-4xl">
          Who&rsquo;s hiring
        </h2>
      </ScrollReveal>

      <div className="mt-14 flex flex-col">
        {companies.map((c, i) => {
          const share = totalPostings > 0 ? (c.posting_count / totalPostings) * 100 : 0;
          return (
            <ScrollReveal
              key={c.company}
              index={i}
              staggerMs={60}
              className="rounded-sm border-b border-cream/15 px-3 py-6 -mx-3 transition-colors duration-150 first:pt-0 last:border-0 hover:bg-cream/5"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
                <div className="flex items-baseline gap-3">
                  <span className="font-mono text-sm text-cream/40">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="font-sans text-base font-medium text-cream">{c.company}</span>
                </div>
                <div className="flex items-baseline gap-5">
                  <span className="font-mono text-lg tabular-nums text-cream/90">
                    {c.posting_count.toLocaleString("en-US")}
                  </span>
                  <span className="font-mono text-sm tabular-nums text-cerulean">
                    {share.toFixed(1)}% of all postings
                  </span>
                </div>
              </div>

              {c.cities.length > 0 && (
                <p className="mt-2 font-sans text-sm text-cream/50">{c.cities.join(", ")}</p>
              )}

              {c.top_skills.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {c.top_skills.map((s) => (
                    <span
                      key={s.skill}
                      className="border border-cream/15 px-2.5 py-1 font-mono text-xs text-cream/70"
                    >
                      {s.display}
                    </span>
                  ))}
                </div>
              )}

              <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs text-cream/40">
                {c.salary_pkr.median !== null && (
                  <span>Median salary: {formatPKR(c.salary_pkr.median)}</span>
                )}
                {c.templated.share !== null && (
                  <span>{Math.round(c.templated.share * 100)}% templated postings</span>
                )}
              </div>
            </ScrollReveal>
          );
        })}
      </div>

      <p className="mt-10 font-mono text-xs text-cream/50">
        Posting volume concentration is itself a market finding; templated-share detects exact
        duplicate skill blocks only.
      </p>
    </div>
  );
}
