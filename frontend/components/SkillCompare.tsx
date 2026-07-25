"use client";

import { useMemo, useState, useTransition } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getSkillsCompare, type SkillsCompareResponse, type SkillTopEntry } from "@/lib/api";
import { formatBucketLabel } from "@/lib/format";
import { Combobox } from "./Combobox";

interface SkillCompareProps {
  skills: SkillTopEntry[];
  initialData: SkillsCompareResponse;
  initialA: string;
  initialB: string;
}

interface ChartRow {
  bucket: string;
  a: number;
  b: number;
}

function toChartRows(data: SkillsCompareResponse): ChartRow[] {
  const len = Math.max(data.a.buckets.length, data.b.buckets.length);
  const rows: ChartRow[] = [];
  for (let i = 0; i < len; i++) {
    const bucketA = data.a.buckets[i];
    const bucketB = data.b.buckets[i];
    // The in-progress week (there is ever at most one) is never plotted --
    // its count is an undercount by construction, and the line must never
    // plunge to a partial bucket.
    if (bucketA?.is_partial || bucketB?.is_partial) continue;
    rows.push({
      bucket: bucketA?.bucket ?? bucketB?.bucket ?? "",
      a: bucketA?.postings_with_skill ?? 0,
      b: bucketB?.postings_with_skill ?? 0,
    });
  }
  return rows;
}

function hasPartialBucket(data: SkillsCompareResponse): boolean {
  return data.a.buckets.some((b) => b.is_partial) || data.b.buckets.some((b) => b.is_partial);
}

function CustomTooltip({
  active,
  payload,
  label,
  aLabel,
  bLabel,
}: {
  active?: boolean;
  payload?: { value: number }[];
  label?: string;
  aLabel: string;
  bLabel: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="border border-java/20 bg-cream px-4 py-3 font-mono text-xs shadow-none">
      <p className="mb-1.5 text-java/60">{label ? formatBucketLabel(label, "week") : ""}</p>
      <p className="text-sceptre">{aLabel}: {payload[0]?.value ?? 0}</p>
      <p className="mt-0.5 text-[#5B7FA6]">{bLabel}: {payload[1]?.value ?? 0}</p>
    </div>
  );
}

export function SkillCompare({ skills, initialData, initialA, initialB }: SkillCompareProps) {
  const [skillA, setSkillA] = useState(initialA);
  const [skillB, setSkillB] = useState(initialB);
  const [data, setData] = useState(initialData);
  const [isPending, startTransition] = useTransition();

  const sortedSkills = useMemo(
    () => [...skills].sort((x, y) => x.display.localeCompare(y.display)),
    [skills]
  );

  function updateCompare(a: string, b: string) {
    setSkillA(a);
    setSkillB(b);
    startTransition(async () => {
      // apiFetch never throws -- null means keep showing the last good
      // data rather than a broken chart.
      const fresh = await getSkillsCompare(a, b);
      if (fresh) setData(fresh);
    });
  }

  const rows = toChartRows(data);
  const isSparse = rows.length < 3;
  const weekInProgress = hasPartialBucket(data);
  const options = sortedSkills.map((s) => ({ value: s.skill, label: s.display }));

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
        <Combobox
          options={options}
          value={skillA}
          onChange={(v) => updateCompare(v, skillB)}
          ariaLabel="First skill to compare"
          dotClassName="bg-sceptre"
          variant="cream"
          className="w-full sm:w-56"
        />
        <span className="font-mono text-xs uppercase tracking-widest text-java/40">vs</span>
        <Combobox
          options={options}
          value={skillB}
          onChange={(v) => updateCompare(skillA, v)}
          ariaLabel="Second skill to compare"
          dotClassName="bg-[#5B7FA6]"
          variant="cream"
          className="w-full sm:w-56"
        />
      </div>

      <div
        className={`mt-8 h-72 transition-opacity duration-200 md:h-80 ${isPending ? "opacity-50" : "opacity-100"}`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
            <CartesianGrid stroke="#23181514" vertical={false} />
            <XAxis
              dataKey="bucket"
              tickFormatter={(v: string) => formatBucketLabel(v, data.a.granularity)}
              tick={{ fontFamily: "var(--font-mono)", fontSize: 11, fill: "#23181599" }}
              axisLine={{ stroke: "#23181533" }}
              tickLine={false}
            />
            <YAxis
              tick={{ fontFamily: "var(--font-mono)", fontSize: 11, fill: "#23181599" }}
              axisLine={false}
              tickLine={false}
              allowDecimals={false}
              width={36}
            />
            <Tooltip
              content={<CustomTooltip aLabel={data.a.display} bLabel={data.b.display} />}
              cursor={{ stroke: "#23181533" }}
            />
            <Line
              type="monotone"
              dataKey="a"
              stroke="#4D0E12"
              strokeWidth={2.5}
              dot={isSparse ? { r: 5, strokeWidth: 0, fill: "#4D0E12" } : false}
              activeDot={{ r: 4, fill: "#4D0E12" }}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="b"
              stroke="#5B7FA6"
              strokeWidth={2.5}
              strokeDasharray="5 4"
              dot={isSparse ? { r: 5, strokeWidth: 0, fill: "#5B7FA6" } : false}
              activeDot={{ r: 4, fill: "#5B7FA6" }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Caption and footnote always sit in normal flow beneath the chart, so
          neither can ever overlap the x-axis tick labels at any width. */}
      <div className="mt-6 space-y-1.5">
        {isSparse && (
          <p className="font-mono text-xs text-java/60">
            One week of history so far — this chart earns its shape as collection continues.
          </p>
        )}
        {weekInProgress && (
          <p className="font-mono text-xs text-java/45">
            This week is still in progress and isn&rsquo;t shown yet — its count would only mislead.
          </p>
        )}
        <p className="font-mono text-xs text-java/45">
          Distinct postings first seen mentioning each skill, by week.
        </p>
      </div>
    </div>
  );
}
