"use client";

import { useState } from "react";
import type { BacktestSummaryResponse } from "@/lib/api";
import { formatSourceScope } from "@/lib/format";

function TargetRow({ target }: { target: BacktestSummaryResponse["by_target"][number] }) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1 border-t border-dashed border-cerulean/30 py-4 first:border-t-0 sm:grid-cols-[1.3fr_1fr_1fr_1fr_1fr_1fr]">
      <p className="col-span-2 font-sans text-sm text-java/90 sm:col-span-1">{target.display}</p>
      <p className="font-mono text-xs tabular-nums text-java/50">
        <span className="text-java/35">weeks </span>
        {target.n}
      </p>
      <p className="font-mono text-xs tabular-nums text-java/50">
        <span className="text-java/35">avg error </span>
        {target.mae !== null ? target.mae.toFixed(2) : "—"}
      </p>
      <p className="font-mono text-xs tabular-nums text-sceptre-bright">
        <span className="text-java/35">beat </span>
        {target.beat}
      </p>
      <p className="font-mono text-xs tabular-nums text-java/45">
        <span className="text-java/35">tie </span>
        {target.tie}
      </p>
      <p className="font-mono text-xs tabular-nums text-java/35">
        <span className="text-java/35">lost </span>
        {target.lost}
      </p>
    </div>
  );
}

export function BacktestPanel({ backtest }: { backtest: BacktestSummaryResponse }) {
  const [expanded, setExpanded] = useState(false);
  const { overall, by_target, n_weeks, source_scope } = backtest;
  const targetCount = by_target.length;

  return (
    <>
      <div className="mt-6 border-t border-dashed border-cerulean/30 pt-6">
        <p className="font-sans text-sm text-java/80">
          {n_weeks} week{n_weeks === 1 ? "" : "s"} backtested across {targetCount} target
          {targetCount === 1 ? "" : "s"} ({formatSourceScope(source_scope)}): {overall.beat} beat,{" "}
          {overall.tie} tied, {overall.lost} lost.
        </p>

        {/* A 1-week trailing mean equals its own single input, so the model's
            prediction is arithmetically identical to the baseline by
            construction -- every outcome ties, not because the model has no
            skill, but because there's nothing yet to tell them apart. Stating
            this once beats headlining a "0%" that reads as a failing grade. */}
        {n_weeks <= 1 && (
          <p className="mt-2 max-w-xl font-sans text-sm text-java/60">
            With one week of history the model and the baseline are arithmetically identical, so all
            results are ties and the backtest has no discriminating power yet.
          </p>
        )}
      </div>

      {targetCount > 0 && (
        <>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-4 cursor-pointer font-mono text-xs uppercase tracking-widest text-java/50 underline underline-offset-4 transition-colors duration-150 hover:text-java/80"
          >
            {expanded ? "Hide the full table" : `Show the full table (${targetCount} targets)`}
          </button>

          {expanded && (
            <div className="mt-2">
              {by_target.map((t) => (
                <TargetRow key={`${t.target_type}-${t.target_key}`} target={t} />
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}
