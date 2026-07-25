import {
  getBacktestSummary,
  getBriefingsLatest,
  getForecastsAccuracy,
  getForecastsPending,
  getSystemHealth,
  type BacktestSummaryResponse,
  type BacktestTargetSummary,
  type BriefingsLatestResponse,
  type ForecastsAccuracyResponse,
  type ForecastsPendingResponse,
  type GradedForecast,
  type PendingForecast,
  type SystemHealth,
} from "@/lib/api";
import { formatPercent, relativeTime } from "@/lib/format";
import { ScrollReveal } from "../ScrollReveal";
import { SectionDivider } from "../SectionDivider";

const FORECASTS_EMPTY_COPY = "First forecasts logged July 18, 2026. First grades arrive July 27.";

// The rest of the engine room (system health, curious findings elsewhere on
// the page) shouldn't go dark just because forecasting data isn't ready --
// e.g. between this code shipping and the one-time migration/first
// --predict run. lib/api.ts's apiFetch never throws (returns null on
// failure); these fall back to the same shape a genuinely-empty table
// would produce, which the existing rendering below already displays
// honestly ("—", empty sections) -- nothing more drastic needed.
async function safeGetSystemHealth(): Promise<SystemHealth> {
  return (
    (await getSystemHealth()) ?? {
      last_successful_run_per_source: {},
      postings_added_24h: 0,
      postings_added_7d: 0,
      enrichment_coverage_mustakbil: null,
      extraction_coverage: null,
      data_freshness_hours: null,
      table_sizes: {},
      checked_at: "",
    }
  );
}

async function safeGetForecastsPending(): Promise<ForecastsPendingResponse> {
  return (await getForecastsPending()) ?? { count: 0, forecasts: [] };
}

async function safeGetForecastsAccuracy(): Promise<ForecastsAccuracyResponse> {
  return (
    (await getForecastsAccuracy()) ?? {
      forecasts: [],
      summary: {
        count_graded: 0,
        mae_overall: null,
        beat_baseline_rate_overall: null,
        beat_baseline_rate_by_type: {},
        outcome_counts_overall: { beat: 0, tie: 0, lost: 0 },
      },
    }
  );
}

async function safeGetBacktestSummary(): Promise<BacktestSummaryResponse> {
  return (
    (await getBacktestSummary()) ?? {
      n_weeks: 0,
      n_rows: 0,
      source_scope: null,
      overall: { n: 0, beat: 0, tie: 0, lost: 0, beat_rate: null, mae: null },
      by_target: [],
    }
  );
}

async function safeGetBriefingsLatest(): Promise<BriefingsLatestResponse> {
  return (await getBriefingsLatest()) ?? { has_briefing: false };
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

function formatSourceScope(scope: string | null): string {
  if (!scope) return "—";
  return scope.split(",").map((s) => s.trim()).filter(Boolean).join(" + ");
}

function GradedRow({ forecast, index }: { forecast: GradedForecast; index: number }) {
  return (
    <ScrollReveal
      index={index}
      staggerMs={60}
      className="grid grid-cols-2 gap-x-4 gap-y-1 border-t border-java/15 py-4 first:border-t-0 sm:grid-cols-[1.3fr_1fr_1fr_1fr_1fr_1.2fr]"
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
      <p
        className={`font-mono text-xs ${
          forecast.outcome === "beat"
            ? "text-sceptre-bright"
            : forecast.outcome === "tie"
              ? "text-java/50"
              : "text-java/35"
        }`}
      >
        {forecast.outcome === "beat" ? "beat baseline" : forecast.outcome === "tie" ? "tied baseline" : "lost to baseline"}
      </p>
      <p className="font-mono text-xs text-java/40">
        <span className="text-java/35">scope </span>{formatSourceScope(forecast.source_scope)}
      </p>
    </ScrollReveal>
  );
}

function BriefingPanel({ briefing, index }: { briefing: BriefingsLatestResponse; index: number }) {
  return (
    <ScrollReveal
      index={index}
      staggerMs={70}
      className="border border-java/15 bg-cream px-6 py-6 md:px-10 md:py-8"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">Weekly briefing</p>
          {briefing.has_briefing && (
            <h3 className="mt-3 max-w-xl font-display text-lg font-medium leading-relaxed md:text-xl">
              Week of {briefing.week_start}
            </h3>
          )}
        </div>
        {briefing.has_briefing && (
          <p className="font-mono text-xs uppercase tracking-widest text-java/40">
            {briefing.source === "llm" ? "drafted by model" : "template fallback"}
          </p>
        )}
      </div>

      {briefing.has_briefing ? (
        <>
          <p className="mt-5 max-w-2xl font-sans text-[15px] leading-relaxed text-java/85">
            {briefing.body}
          </p>
          <p className="mt-6 font-sans text-xs text-java/45">
            Generated from computed facts and machine-verified before publication -- the model
            never computes or predicts, and every number and name above traces back to the
            underlying data.
          </p>
        </>
      ) : (
        <p className="mt-4 font-sans text-sm text-java/60">{FORECASTS_EMPTY_COPY}</p>
      )}
    </ScrollReveal>
  );
}

function BacktestTargetRow({ target, index }: { target: BacktestTargetSummary; index: number }) {
  return (
    <ScrollReveal
      index={index}
      staggerMs={60}
      className="grid grid-cols-2 gap-x-4 gap-y-1 border-t border-dashed border-cerulean/30 py-4 first:border-t-0 sm:grid-cols-[1.3fr_1fr_1fr_1fr_1fr_1fr]"
    >
      <p className="col-span-2 font-sans text-sm text-java/90 sm:col-span-1">{target.display}</p>
      <p className="font-mono text-xs tabular-nums text-java/50">
        <span className="text-java/35">n </span>{target.n}
      </p>
      <p className="font-mono text-xs tabular-nums text-java/50">
        <span className="text-java/35">mae </span>{target.mae !== null ? target.mae.toFixed(2) : "—"}
      </p>
      <p className="font-mono text-xs tabular-nums text-sceptre-bright">
        <span className="text-java/35">beat </span>{target.beat}
      </p>
      <p className="font-mono text-xs tabular-nums text-java/45">
        <span className="text-java/35">tie </span>{target.tie}
      </p>
      <p className="font-mono text-xs tabular-nums text-java/35">
        <span className="text-java/35">lost </span>{target.lost}
      </p>
    </ScrollReveal>
  );
}

export async function EngineRoom() {
  const [health, briefing, pending, accuracy, backtest] = await Promise.all([
    safeGetSystemHealth(),
    safeGetBriefingsLatest(),
    safeGetForecastsPending(),
    safeGetForecastsAccuracy(),
    safeGetBacktestSummary(),
  ]);
  const sources = Object.entries(health.last_successful_run_per_source);
  const outcomeCounts = accuracy.summary.outcome_counts_overall;
  const totalGraded = accuracy.summary.count_graded;

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

        {/* WEEKLY BRIEFING -- above Pending, per spec: the fully-automated,
            fact-gated narrative (briefing.py). See BriefingPanel. */}
        <BriefingPanel briefing={briefing} index={sources.length + 3} />

        {/* PENDING -- this week's predictions, logged and waiting to be
            checked against reality. Soil panel, matching the register the
            "Forecasts" placeholder used before real forecasting existed. */}
        <ScrollReveal
          index={sources.length + 4}
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
          index={sources.length + 5}
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
            {totalGraded > 0 && (
              <div className="text-right">
                <p className="font-mono text-2xl font-semibold tabular-nums">
                  <span className="text-sceptre-bright">beat {outcomeCounts.beat}</span>
                  <span className="text-java/30">, </span>
                  <span className="text-java/60">tied {outcomeCounts.tie}</span>
                  <span className="text-java/30">, </span>
                  <span className="text-java/35">lost {outcomeCounts.lost}</span>
                </p>
                <p className="font-mono text-xs uppercase tracking-widest text-java/50">
                  of {totalGraded} graded
                </p>
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
            Forecasts are graded against the data sources they were computed from; sources added
            later do not retroactively change past grades.
          </p>
          <p className="mt-2 font-sans text-xs text-java/45">
            Volume forecasts cover automated collection only (Mustakbil); skill forecasts cover
            automated sources, excluding the bulk poster.
          </p>
        </ScrollReveal>

        {/* BACKTEST -- retrospective only, deliberately below and visually
            distinct from PENDING/GRADED LOG above: a dashed cerulean border
            and diagonal hatch fill instead of the solid java/cream/soil
            registers used everywhere else in this room, so this can never
            be misread as more live-forecast evidence. */}
        <ScrollReveal
          index={sources.length + 6}
          staggerMs={70}
          className="border border-dashed border-cerulean/50 bg-[repeating-linear-gradient(135deg,rgba(165,188,214,0.12)_0px,rgba(165,188,214,0.12)_1px,transparent_1px,transparent_12px)] px-6 py-8 md:px-10 md:py-10"
        >
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-cerulean">
            Backtest — retrospective
          </p>
          <h3 className="mt-3 max-w-xl font-display text-lg font-medium leading-relaxed md:text-xl">
            Would the model have worked, in hindsight?
          </h3>
          <p className="mt-3 max-w-xl font-sans text-sm text-java/60">
            Computed after outcomes were already known, over every historical week with prior
            Mustakbil history — this is weaker evidence than the log above. A backtest shows
            whether the model has any skill at all; only the live forecast log proves a
            prediction was made before the outcome existed.
          </p>

          {backtest.n_rows > 0 ? (
            <>
              <div className="mt-6 flex flex-wrap items-end justify-between gap-6 border-t border-dashed border-cerulean/30 pt-6">
                <div>
                  <p className="font-mono text-xs uppercase tracking-widest text-java/50">
                    {backtest.n_weeks} week{backtest.n_weeks === 1 ? "" : "s"} backtested
                  </p>
                  <p className="mt-1 font-mono text-xs text-java/40">
                    source_scope {backtest.source_scope ?? "—"}
                  </p>
                </div>
                {backtest.overall.beat_rate !== null && (
                  <div className="text-right">
                    <p className="font-mono text-4xl font-semibold tabular-nums text-cerulean">
                      {formatPercent(backtest.overall.beat_rate)}
                    </p>
                    <p className="font-mono text-xs uppercase tracking-widest text-java/50">
                      beat baseline (retrospective)
                    </p>
                  </div>
                )}
              </div>

              <div className="mt-2">
                {backtest.by_target.map((t, i) => (
                  <BacktestTargetRow key={`${t.target_type}-${t.target_key}`} target={t} index={i} />
                ))}
              </div>
            </>
          ) : (
            <p className="mt-6 font-sans text-sm text-java/60">
              No backtest computed yet — run <code>python backtest.py</code> once Mustakbil has at
              least two complete weeks of history.
            </p>
          )}
        </ScrollReveal>
      </div>
    </div>
  );
}
