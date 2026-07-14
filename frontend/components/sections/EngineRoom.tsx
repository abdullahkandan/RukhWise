import { getSystemHealth } from "@/lib/api";
import { formatPercent, relativeTime } from "@/lib/format";
import { ScrollReveal } from "../ScrollReveal";
import { SectionDivider } from "../SectionDivider";

function CoverageBar({ label, value }: { label: string; value: number | null }) {
  return (
    <div>
      <div className="flex items-baseline justify-between font-mono text-xs">
        <span className="uppercase tracking-widest text-java/50">{label}</span>
        <span className="tabular-nums text-java/80">{value !== null ? formatPercent(value) : "—"}</span>
      </div>
      <div className="mt-2 h-[3px] bg-java/10">
        <div className="h-full bg-java" style={{ width: `${(value ?? 0) * 100}%` }} />
      </div>
    </div>
  );
}

export async function EngineRoom() {
  const health = await getSystemHealth();
  const sources = Object.entries(health.last_successful_run_per_source);

  return (
    <div className="py-24 md:py-32">
      <ScrollReveal>
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">
          The engine room
        </p>
        <h2 className="mt-3 max-w-2xl font-display text-3xl font-medium leading-tight md:text-4xl">
          How the machine is doing
        </h2>
        <p className="mt-3 max-w-lg font-sans text-sm text-java/60">
          This system collects, grades, and monitors itself. This is its pulse.
        </p>
      </ScrollReveal>

      <SectionDivider className="mt-10" />

      <div className="mt-4 flex flex-col gap-4">
        {/* Status tiles: count varies with the number of collection sources, so
            this row uses auto-fit rather than a fixed track count — any number
            of tiles fills the row on its own terms, never leaving a gap. */}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4">
          {sources.map(([source, timestamp], i) => (
            <ScrollReveal
              key={source}
              index={i}
              staggerMs={70}
              className="border border-java/15 bg-cream px-6 py-6"
            >
              <p className="font-mono text-xs uppercase tracking-widest text-java/50">{source}</p>
              <p className="mt-2 font-mono text-2xl tabular-nums">{relativeTime(timestamp)}</p>
              <p className="mt-1 font-sans text-xs text-java/50">last successful run</p>
            </ScrollReveal>
          ))}

          <ScrollReveal
            index={sources.length}
            staggerMs={70}
            className="border border-java/15 bg-cream px-6 py-6"
          >
            <p className="font-mono text-xs uppercase tracking-widest text-java/50">Freshness</p>
            <p className="mt-2 font-mono text-2xl tabular-nums">
              {health.data_freshness_hours !== null ? `${Math.round(health.data_freshness_hours)}h` : "—"}
            </p>
            <p className="mt-1 font-sans text-xs text-java/50">since last touched record</p>
          </ScrollReveal>
        </div>

        {/* Detail panels: always exactly two, so a fixed 2-column split never
            has a remainder to leave hanging. */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <ScrollReveal
            index={sources.length + 1}
            staggerMs={70}
            className="border border-java/15 bg-cream px-6 py-6"
          >
            <div className="flex flex-col gap-4">
              <CoverageBar label="Mustakbil enrichment" value={health.enrichment_coverage_mustakbil} />
              <CoverageBar label="Skill extraction" value={health.extraction_coverage} />
            </div>
          </ScrollReveal>

          <ScrollReveal
            index={sources.length + 2}
            staggerMs={70}
            className="border border-java/15 bg-cream px-6 py-6"
          >
            <p className="font-mono text-xs uppercase tracking-widest text-java/50">Table sizes</p>
            <div className="mt-3 flex gap-8">
              {Object.entries(health.table_sizes).map(([table, count]) => (
                <div key={table}>
                  <p className="font-mono text-2xl tabular-nums">{count.toLocaleString("en-US")}</p>
                  <p className="font-sans text-xs text-java/50">{table.replace("_", " ")}</p>
                </div>
              ))}
            </div>
          </ScrollReveal>
        </div>

        <ScrollReveal
          index={sources.length + 3}
          staggerMs={70}
          className="border border-java/15 bg-soil px-8 py-8 text-cream md:px-10 md:py-10"
        >
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-cream/50">Forecasts</p>
          <p className="mt-4 max-w-2xl font-display text-lg font-medium leading-relaxed text-cream/90 md:text-xl">
            First forecast pending sufficient history. This system logs predictions before
            outcomes are known, then grades itself.
          </p>
        </ScrollReveal>
      </div>
    </div>
  );
}
