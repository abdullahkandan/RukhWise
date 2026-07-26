/** Shared formatting helpers — kept out of components so number/date
 * formatting stays consistent everywhere it appears. */

export function formatNumber(n: number): string {
  return Math.round(n).toLocaleString("en-US");
}

export function formatPKR(n: number): string {
  return `₨${Math.round(n).toLocaleString("en-US")}`;
}

export function formatPercent(n: number, digits = 0): string {
  return `${(n * 100).toFixed(digits)}%`;
}

/** Hours since an ISO timestamp, as a small integer. */
export function hoursSince(isoTimestamp: string): number {
  const then = new Date(isoTimestamp).getTime();
  const now = Date.now();
  return Math.max(0, Math.round((now - then) / (1000 * 60 * 60)));
}

/** Coarse relative-time label ("3h ago", "2d ago") for run timestamps. */
export function relativeTime(isoTimestamp: string): string {
  const hours = hoursSince(isoTimestamp);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  return `${weeks}w ago`;
}

/** Light city normalization for display -- trim + title case. The API
 * already does this for /cities/breakdown, but recent-postings and other
 * raw fields benefit from the same treatment. */
export function titleCaseCity(city: string): string {
  return city
    .trim()
    .split(/\s+/)
    .map((w) => (w.length > 0 ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w))
    .join(" ");
}

/** "mustakbil,rozee" -> "Mustakbil + Rozee". Shared by the forecast and
 * backtest panels on /engine -- both surface the same raw comma-joined
 * source-key string from the API. */
export function formatSourceScope(scope: string | null): string {
  if (!scope) return "—";
  return scope
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(" + ");
}

export function formatBucketLabel(bucket: string, granularity: string): string {
  const date = new Date(bucket);
  if (granularity === "day") {
    return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }
  return `wk of ${date.toLocaleDateString("en-US", { month: "short", day: "numeric" })}`;
}
