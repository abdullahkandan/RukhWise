"use client";

import { useState } from "react";
import type { PendingForecast } from "@/lib/api";
import { relativeTime } from "@/lib/format";

// The volume forecast (there are at most a couple of these, one per pending
// week) always shows in full. Skill forecasts are the bulk of the list --
// show the first few, collapse the rest behind a toggle. "Top" here just
// means the API's own deterministic order (week, then target key); there is
// no ranking signal to sort by yet.
const DEFAULT_SKILL_COUNT = 5;

function Row({ forecast }: { forecast: PendingForecast }) {
  // A zero-width interval (low === high) is not a real range -- it's what
  // the model produces when there isn't yet enough trailing history to
  // compute one, and displaying it as a bracket claims a certainty the
  // forecast doesn't have. Show the point prediction alone; the section-
  // level note above explains why some rows lack a range.
  const hasRange = forecast.interval_low !== forecast.interval_high;
  return (
    <div className="border-t border-cream/15 py-4 first:border-t-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <p className="font-sans text-sm text-cream/90">{forecast.display}</p>
        <p className="font-mono text-sm tabular-nums text-cream/90">
          {forecast.predicted.toFixed(1)}
          {hasRange && (
            <span className="text-cream/45">
              {" "}
              [{forecast.interval_low.toFixed(1)}–{forecast.interval_high.toFixed(1)}]
            </span>
          )}
        </p>
      </div>
      <p className="mt-1 font-mono text-xs text-cream/40">
        logged {relativeTime(forecast.created_at)} · for the week of {forecast.target_week_start}
      </p>
    </div>
  );
}

export function PendingForecasts({
  forecasts,
  count,
}: {
  forecasts: PendingForecast[];
  count: number;
}) {
  const [expanded, setExpanded] = useState(false);

  const volumeRows = forecasts.filter((f) => f.target_type === "volume");
  const skillRows = forecasts.filter((f) => f.target_type === "skill");
  const defaultRows = [...volumeRows, ...skillRows.slice(0, DEFAULT_SKILL_COUNT)];
  const restRows = skillRows.slice(DEFAULT_SKILL_COUNT);

  const anyRegimeNote = forecasts.some((f) => f.collection_regime_note);
  const anyMissingInterval = forecasts.some((f) => f.interval_low === f.interval_high);

  const rowKey = (f: PendingForecast) => `${f.target_type}-${f.target_key}-${f.target_week_start}`;

  return (
    <div className="mt-6">
      {anyRegimeNote && (
        <p className="mb-4 font-mono text-xs uppercase tracking-[0.08em] text-cream/70">
          Some of these were computed across a collection-cadence change (initial bulk backfill vs.
          daily collection) — their error reflects that, not forecast skill.
        </p>
      )}
      {anyMissingInterval && (
        <p className="mb-4 font-sans text-xs text-cream/50">
          Ranges are missing on some rows below — there wasn&rsquo;t yet enough history to compute one
          for those first batches.
        </p>
      )}

      {defaultRows.map((f) => (
        <Row key={rowKey(f)} forecast={f} />
      ))}

      {restRows.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-4 cursor-pointer font-mono text-xs uppercase tracking-widest text-cream/50 underline underline-offset-4 transition-colors duration-150 hover:text-cream/80"
          >
            {expanded ? "Show fewer" : `Show all ${count}`}
          </button>
          {expanded && (
            <div className="mt-2">
              {restRows.map((f) => (
                <Row key={rowKey(f)} forecast={f} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
