import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { Footer } from "@/components/Footer";
import { CountUp } from "@/components/CountUp";
import { DataUnavailable } from "@/components/DataUnavailable";
import { getCurriculumAlignment } from "@/lib/api";
import { formatNumber } from "@/lib/format";

export const metadata: Metadata = {
  title: "Curriculum Alignment — Rukhwise",
  description: "Where Pakistan's computing curricula and computing-sector job demand actually meet, and where they don't.",
};

// The page is a summary, not a database dump. Full lists stay available on
// /curriculum/alignment; a visitor gets the head of each and a count.
const TOP_N = 10;

function Prose({ children }: { children: React.ReactNode }) {
  return <div className="mt-4 max-w-2xl font-sans text-[15px] leading-relaxed text-java/75">{children}</div>;
}

function SkillRow({
  rank,
  display,
  category,
  primaryLabel,
  primaryValue,
  secondaryLabel,
  secondaryValue,
  dark = false,
}: {
  rank: number;
  display: string;
  category: string;
  primaryLabel: string;
  primaryValue: number;
  secondaryLabel: string;
  secondaryValue: number;
  dark?: boolean;
}) {
  const rankClass = dark ? "text-cream/40" : "text-java/40";
  const displayClass = dark ? "text-cream" : "text-java";
  const categoryClass = dark ? "text-cream/45" : "text-java/45";
  const rowBorder = dark ? "border-cream/10 hover:border-cream/25 hover:bg-cream/5" : "border-java/10 hover:border-java/25 hover:bg-java/5";
  const valueClass = dark ? "text-cream/85" : "text-java/85";
  const labelClass = dark ? "text-cream/40" : "text-java/40";

  return (
    <li className={`grid grid-cols-[2rem_1fr_auto] items-center gap-4 rounded-sm border-b px-2 py-2.5 transition-colors duration-150 -mx-2 ${rowBorder}`}>
      <span className={`font-mono text-xs ${rankClass}`}>{String(rank).padStart(2, "0")}</span>
      <div>
        <p className={`font-sans text-sm font-medium ${displayClass}`}>{display}</p>
        <p className={`mt-0.5 font-mono text-[11px] uppercase tracking-[0.14em] ${categoryClass}`}>{category.replace(/_/g, " ")}</p>
      </div>
      <div className="flex flex-col items-end gap-1">
        <span className={`font-mono text-sm tabular-nums ${valueClass}`}>
          {formatNumber(primaryValue)} <span className={`text-[11px] font-normal ${labelClass}`}>{primaryLabel}</span>
        </span>
        <span className={`font-mono text-xs tabular-nums ${labelClass}`}>
          {formatNumber(secondaryValue)} {secondaryLabel}
        </span>
      </div>
    </li>
  );
}

export default async function CurriculumPage() {
  const data = await getCurriculumAlignment();

  if (!data) {
    return (
      <main>
        <Section register="cream" className="pt-36 pb-20 md:pt-44 md:pb-24">
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">Curriculum Alignment</p>
          <h1 className="mt-3 max-w-2xl font-display text-4xl font-medium leading-tight md:text-5xl">
            What&rsquo;s taught, what&rsquo;s hired
          </h1>
          <DataUnavailable message="The curriculum alignment index is temporarily unavailable." />
        </Section>
        <Footer />
      </main>
    );
  }

  const domainLabel = data.market_domains.map((d) => d.replace(/_/g, " ")).join(" + ");

  return (
    <main>
      <Section register="cream" className="pt-36 pb-20 md:pt-44 md:pb-24">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">Curriculum Alignment</p>
        <h1 className="mt-3 max-w-2xl font-display text-4xl font-medium leading-tight md:text-5xl">
          What&rsquo;s taught, what&rsquo;s hired
        </h1>

        <div className="mt-8 max-w-2xl border-l-2 border-java/30 pl-5">
          <p className="font-sans text-base leading-relaxed text-java/80">{data.scope_note}</p>
        </div>

        <div className="mt-12 grid grid-cols-2 gap-8 md:grid-cols-4">
          <div>
            <CountUp value={data.courses_total} className="font-display text-3xl font-medium" />
            <p className="mt-1 font-mono text-xs uppercase tracking-[0.14em] text-java/50">Courses parsed</p>
          </div>
          <div>
            <CountUp value={data.courses_matched} className="font-display text-3xl font-medium" />
            <p className="mt-1 font-mono text-xs uppercase tracking-[0.14em] text-java/50">Matched a tracked skill</p>
          </div>
          <div>
            <CountUp value={data.market_postings_considered} className="font-display text-3xl font-medium" />
            <p className="mt-1 font-mono text-xs uppercase tracking-[0.14em] text-java/50">Postings considered</p>
          </div>
          <div>
            <CountUp value={data.demanded_not_taught.length} className="font-display text-3xl font-medium" />
            <p className="mt-1 font-mono text-xs uppercase tracking-[0.14em] text-java/50">Demanded, not taught</p>
          </div>
        </div>
      </Section>

      {/* Primary finding -- demanded, not taught */}
      <Section register="java" className="py-20 md:py-28">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-cream/50">The headline finding</p>
        <h2 className="mt-3 max-w-2xl font-display text-3xl font-medium leading-tight md:text-4xl">
          Demanded in the market, absent from the curriculum
        </h2>
        <p className="mt-4 max-w-2xl font-sans text-[15px] leading-relaxed text-cream/70">
          Asked for by at least 5 companies. Taught in neither curriculum.
        </p>

        <ol className="mt-10 flex flex-col gap-1">
          {data.demanded_not_taught.slice(0, TOP_N).map((row, i) => (
            <SkillRow
              key={row.skill}
              rank={i + 1}
              display={row.display}
              category={row.category}
              primaryLabel="postings"
              primaryValue={row.posting_count}
              secondaryLabel="companies"
              secondaryValue={row.company_count}
              dark
            />
          ))}
          {data.demanded_not_taught.length === 0 && (
            <li className="font-sans text-sm text-cream/50">No qualifying gaps found.</li>
          )}
        </ol>
        {data.demanded_not_taught.length > TOP_N && (
          <p className="mt-6 font-mono text-xs text-cream/40">
            Top {TOP_N} of {data.demanded_not_taught.length}, by company count.
          </p>
        )}
      </Section>

      {/* Confirmation -- taught and demanded */}
      <Section register="cream" className="py-20 md:py-28">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">Where it does line up</p>
        <h2 className="mt-3 max-w-2xl font-display text-3xl font-medium leading-tight md:text-4xl">
          Taught and demanded
        </h2>
        <p className="mt-4 max-w-2xl font-sans text-[15px] leading-relaxed text-java/70">
          In both places, ranked by postings. What the curricula already get right.
        </p>

        <ol className="mt-10 flex flex-col gap-1">
          {data.taught_and_demanded.slice(0, TOP_N).map((row, i) => (
            <SkillRow
              key={row.skill}
              rank={i + 1}
              display={row.display}
              category={row.category}
              primaryLabel="postings"
              primaryValue={row.posting_count}
              secondaryLabel={`course${row.course_count === 1 ? "" : "s"}`}
              secondaryValue={row.course_count ?? 0}
            />
          ))}
          {data.taught_and_demanded.length === 0 && (
            <li className="font-sans text-sm text-java/50">No overlap found.</li>
          )}
        </ol>
        {data.taught_and_demanded.length > TOP_N && (
          <p className="mt-6 font-mono text-xs text-java/40">
            Top {TOP_N} of {data.taught_and_demanded.length}.
          </p>
        )}
      </Section>

      {/* Methodology */}
      <Section register="cream" className="pb-24 pt-4 md:pb-32">
        <h2 className="font-display text-2xl font-medium">Methodology</h2>
        <Prose>
          <ul className="flex flex-col gap-4">
            <li>
              <strong className="text-java">Sources and scope.</strong> NCEAC&rsquo;s BS Computing
              Disciplines (2023) and HEC&rsquo;s Computer Science booklet (2025), compared against{" "}
              {domainLabel} postings — {formatNumber(data.market_postings_considered)} of them.
            </li>
            <li>
              <strong className="text-java">What counts as a skill.</strong> {data.demanded_not_taught_note}
            </li>
            <li>
              <strong className="text-java">Taxonomy-based matching, both ways.</strong> {data.matching_note}
            </li>
            <li>
              <strong className="text-java">Curricula state minimums.</strong> They define a floor, not a
              ceiling — universities routinely exceed them with electives and specializations neither
              document lists. &ldquo;Not taught&rdquo; means not in the national minimum curriculum, not
              that no graduate has encountered it.
            </li>
            <li>
              <strong className="text-java">Course parsing is imperfect.</strong> Both PDFs mix table
              layouts, template course codes, and free-text descriptions.{" "}
              {formatNumber(data.courses_total)} courses were extracted;{" "}
              {formatNumber(data.courses_matched)} matched at least one taxonomy skill by title or stated
              topics. A course matching nothing may teach no tracked skill by name, or may simply lack
              topic text in the source — both are reported as unmatched, not silently distinguished.
            </li>
          </ul>
        </Prose>
      </Section>

      <Footer />
    </main>
  );
}
