import {
  getForecastsAccuracy,
  getForecastsPending,
  getSystemHealth,
  type ForecastsAccuracyResponse,
  type ForecastsPendingResponse,
  type GradedForecast,
  type PendingForecast,
} from "@/lib/api";
import { formatPercent, relativeTime } from "@/lib/format";
import { ScrollReveal } from "../ScrollReveal";
import { SectionDivider } from "../SectionDivider";

const FORECASTS_EMPTY_COPY = "First forecasts logged July 18, 2026. First grades arrive July 27.";

// The rest of the engine room (system health, curious findings elsewhere on
// the page) shouldn't go dark just because forecasting data isn't ready --
// e.g. between this code shipping and the one-time migration/first
// --predict run. A fetch failure here degrades to the same empty state a
// genuinely-empty table would show, nothing more drastic.
async function safeGetForecastsPending(): Promise<ForecastsPendingResponse> {
  try {
    return await getForecastsPending();
  } catch {
    return { count: 0, forecasts: [] };
  }
}

async function safeGetForecastsAccuracy(): Promise<ForecastsAccuracyResponse> {
  try {
    return await getForecastsAccuracy();
  } catch {
    return {
      forecasts: [],
      summary: {
        count_graded: 0,
        mae_overall: null,
        beat_baseline_rate_overall: null,
        beat_baseline_rate_by_type: {},
      },
    };
  }
}

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

function PendingRow({ forecast, index }: { forecast: PendingForecast; index: number }) {
  return (
    <ScrollReveal index={index} staggerMs={60} className="border-t border-cream/15 py-4 first:border-t-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <p className="font-sans text-sm text-cream/90">{forecast.display}</p>
        <p className="font-mono text-sm tabular-nums text-cream/90">
          {forecast.predicted.toFixed(1)}{" "}
          <span className="text-cream/45">
            [{forecast.interval_low.toFixed(1)}–{forecast.interval_high.toFixed(1)}]
          </span>
        </p>
      </div>
      <p className="mt-1 font-mono text-xs text-cream/40">
        logged {relativeTime(forecast.created_at)} · for the week of {forecast.target_week_start}
      </p>
    </ScrollReveal>
  );
}

function GradedRow({ forecast, index }: { forecast: GradedForecast; index: number }) {
  return (
    <ScrollReveal
      index={index}
      staggerMs={60}
      className="grid grid-cols-2 gap-x-4 gap-y-1 border-t border-java/15 py-4 first:border-t-0 sm:grid-cols-[1.6fr_1fr_1fr_1fr_1fr]"
    >
      <p className="col-span-2 font-sans text-sm text-java/90 sm:col-span-1">{forecast.display}</p>
      <p className="font-mono text-xs tabular-nums text-java/50">
        <span className="text-java/35">pred </span>{forecast.predicted.toFixed(1)}
      </p>
      <p className="font-mono text-xs tabular-nums text-java/50">
        <span className="text-java/35">base </span>{forecast.baseline_predicted.toFixed(1)}
      </p>
      <p className="font-mono text-xs tabular-nums text-java">
        <span className="text-java/35">actual </span>{forecast.actual.toFixed(1)}
      </p>
      <p className={`font-mono text-xs ${forecast.beat_baseline ? "text-sceptre-bright" : "text-java/35"}`}>
        {forecast.beat_baseline ? "beat baseline" : "did not beat baseline"}
      </p>
    </ScrollReveal>
  );
}

export async function EngineRoom() {
  const [health, pending, accuracy] = await Promise.all([
    getSystemHealth(),
    safeGetForecastsPending(),
    safeGetForecastsAccuracy(),
  ]);
  const sources = Object.entries(health.last_successful_run_per_source);
  const beatRate = accuracy.summary.beat_baseline_rate_overall;

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

        {/* PENDING -- this week's predictions, logged and waiting to be
            checked against reality. Soil panel, matching the register the
            "Forecasts" placeholder used before real forecasting existed. */}
        <ScrollReveal
          index={sources.length + 3}
          staggerMs={70}
          className="border border-java/15 bg-soil px-8 py-8 text-cream md:px-10 md:py-10"
        >
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-cream/50">Pending</p>
          <h3 className="mt-3 max-w-xl font-display text-lg font-medium leading-relaxed text-cream/90 md:text-xl">
            Logged, awaiting reality.
          </h3>

          {pending.forecasts.length > 0 ? (
            <div className="mt-6">
              {pending.forecasts.map((f, i) => (
                <PendingRow key={`${f.target_type}-${f.target_key}-${f.target_week_start}`} forecast={f} index={i} />
              ))}
            </div>
          ) : (
            <p className="mt-4 font-sans text-sm text-cream/60">{FORECASTS_EMPTY_COPY}</p>
          )}
        </ScrollReveal>

        {/* GRADED LOG -- every forecast checked against what actually
            happened, headlined by the overall beat-baseline rate. */}
        <ScrollReveal
          index={sources.length + 4}
          staggerMs={70}
          className="border border-java/15 bg-cream px-6 py-6 md:px-10 md:py-8"
        >
          <div className="flex flex-wrap items-end justify-between gap-6">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">Graded log</p>
              <h3 className="mt-3 max-w-xl font-display text-lg font-medium leading-relaxed md:text-xl">
                How the predictions held up
              </h3>
            </div>
            {beatRate !== null && (
              <div className="text-right">
                <p className="font-mono text-4xl font-semibold tabular-nums text-sceptre-bright">
                  {formatPercent(beatRate)}
                </p>
                <p className="font-mono text-xs uppercase tracking-widest text-java/50">beat baseline</p>
              </div>
            )}
          </div>

          {accuracy.forecasts.length > 0 ? (
            <div className="mt-6">
              {accuracy.forecasts.map((f, i) => (
                <GradedRow key={`${f.target_type}-${f.target_key}-${f.target_week_start}`} forecast={f} index={i} />
              ))}
            </div>
          ) : (
            <p className="mt-4 font-sans text-sm text-java/60">{FORECASTS_EMPTY_COPY}</p>
          )}

          <p className="mt-6 font-sans text-xs text-java/45">
            Volume forecasts cover automated collection only (Mustakbil); skill forecasts cover
            all sources, excluding the bulk poster.
          </p>
        </ScrollReveal>
      </div>
    </div>
  );
}
