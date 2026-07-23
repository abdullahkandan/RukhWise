/**
 * All Supabase-backed data comes through here, server-side. Every call
 * revalidates on a 10-minute window (matches the API's own 10-min
 * in-process cache — the collection pipeline runs daily, so this is
 * generous headroom, not a freshness risk). Zero mock data: every function
 * here hits the live FastAPI service.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const REVALIDATE_SECONDS = 600;

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    next: { revalidate: REVALIDATE_SECONDS },
  });
  if (!res.ok) {
    throw new Error(`Rukhwise API ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// /stats/overview
// ---------------------------------------------------------------------------

export interface StatsOverview {
  total_postings: number;
  per_source: Record<string, number>;
  per_category: Record<string, number>;
  distinct_companies: number;
  distinct_cities: number;
  last_collection_at: string | null;
  skill_mention_total: number;
  /** 0-1 fraction: the single highest-volume employer's share of all
   * tracked postings. A single bulk poster can dominate skill/pairing
   * signal -- this is the concentration-disclosure number. */
  top_company_share: number;
}

export function getStatsOverview() {
  return apiFetch<StatsOverview>("/stats/overview");
}

// ---------------------------------------------------------------------------
// /skills/top
// ---------------------------------------------------------------------------

export interface SkillTopEntry {
  skill: string;
  display: string;
  category: string;
  posting_count: number;
  /** Distinct companies demanding this skill -- the bulk-poster-resistant
   * companion to posting_count: a single repeat-poster inflates the latter
   * but not this. */
  company_count: number;
  share_of_postings: number;
}

export interface SkillsTopResponse {
  total_postings: number;
  exclude_bulk: boolean;
  count: number;
  skills: SkillTopEntry[];
}

export function getSkillsTop(params: {
  category?: string;
  includeSoft?: boolean;
  excludeBulk?: boolean;
  limit?: number;
} = {}) {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.includeSoft) qs.set("include_soft", "true");
  if (params.excludeBulk) qs.set("exclude_bulk", "true");
  if (params.limit) qs.set("limit", String(params.limit));
  const query = qs.toString();
  return apiFetch<SkillsTopResponse>(`/skills/top${query ? `?${query}` : ""}`);
}

// ---------------------------------------------------------------------------
// /skills/{skill}/trend and /skills/compare
// ---------------------------------------------------------------------------

export interface TrendBucket {
  bucket: string;
  postings_with_skill: number;
  total_postings: number;
  naive_baseline: number | null;
  /** True only for a bucket still in progress (this week / today) -- its
   * count is an undercount by construction. Charts must render it as
   * visually distinct (hollow point + dashed connector) or exclude it,
   * never plot it as if comparable to a complete bucket. */
  is_partial: boolean;
}

export interface SkillTrend {
  skill: string;
  display: string;
  granularity: string;
  company_count: number;
  buckets: TrendBucket[];
}

export function getSkillTrend(skill: string, granularity: "day" | "week" = "week") {
  return apiFetch<SkillTrend>(`/skills/${encodeURIComponent(skill)}/trend?granularity=${granularity}`);
}

export interface SkillsCompareResponse {
  a: SkillTrend;
  b: SkillTrend;
}

export function getSkillsCompare(a: string, b: string, granularity: "day" | "week" = "week") {
  return apiFetch<SkillsCompareResponse>(
    `/skills/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&granularity=${granularity}`
  );
}

// ---------------------------------------------------------------------------
// /postings/recent
// ---------------------------------------------------------------------------

export interface RecentPosting {
  title: string | null;
  company: string | null;
  city: string | null;
  posting_date: string | null;
  source: string;
  detail_url: string | null;
  skills: string[];
}

export interface PostingsRecentResponse {
  count: number;
  postings: RecentPosting[];
}

export function getPostingsRecent(params: { limit?: number; source?: string } = {}) {
  const qs = new URLSearchParams();
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.source) qs.set("source", params.source);
  const query = qs.toString();
  return apiFetch<PostingsRecentResponse>(`/postings/recent${query ? `?${query}` : ""}`);
}

// ---------------------------------------------------------------------------
// /cities/breakdown
// ---------------------------------------------------------------------------

export interface CityBreakdownEntry {
  city: string;
  count: number;
  raw_variants: string[];
}

export interface CitiesBreakdownResponse {
  skill: string | null;
  total_postings: number;
  no_city_count: number;
  cities: CityBreakdownEntry[];
}

export function getCitiesBreakdown(skill?: string) {
  return apiFetch<CitiesBreakdownResponse>(`/cities/breakdown${skill ? `?skill=${encodeURIComponent(skill)}` : ""}`);
}

// ---------------------------------------------------------------------------
// /salaries/summary
// ---------------------------------------------------------------------------

export interface SalaryStats {
  count: number;
  median: number | null;
  q1: number | null;
  q3: number | null;
  iqr: number | null;
  postings_with_salary?: number;
  total_postings_this_currency?: number;
}

export interface SalariesSummaryResponse {
  currency: string;
  skill: string | null;
  overall: SalaryStats;
  by_experience_band: Record<string, SalaryStats>;
  note: string;
}

export function getSalariesSummary(currency: string = "PKR", skill?: string) {
  const qs = new URLSearchParams({ currency });
  if (skill) qs.set("skill", skill);
  return apiFetch<SalariesSummaryResponse>(`/salaries/summary?${qs.toString()}`);
}

// ---------------------------------------------------------------------------
// /insights/live
// ---------------------------------------------------------------------------

export interface Insight {
  headline: string;
  detail: string;
  value: Record<string, unknown>;
  computed_at: string;
}

export interface InsightsLiveResponse {
  count: number;
  insights: Insight[];
}

export function getInsightsLive() {
  return apiFetch<InsightsLiveResponse>("/insights/live");
}

// ---------------------------------------------------------------------------
// /skills/cooccurrence & /skills/{skill}/companions
// ---------------------------------------------------------------------------

export interface CooccurrencePair {
  skill_a: string;
  display_a: string;
  skill_b: string;
  display_b: string;
  joint_count: number;
  count_a: number;
  count_b: number;
  p_b_given_a: number;
  p_a_given_b: number;
  lift: number;
}

export interface CooccurrenceResponse {
  total_postings: number;
  min_joint_count: number;
  exclude_bulk: boolean;
  count: number;
  pairs: CooccurrencePair[];
}

export function getSkillsCooccurrence(
  params: { includeSoft?: boolean; excludeBulk?: boolean; limit?: number } = {}
) {
  const qs = new URLSearchParams();
  if (params.includeSoft) qs.set("include_soft", "true");
  if (params.excludeBulk) qs.set("exclude_bulk", "true");
  if (params.limit) qs.set("limit", String(params.limit));
  const query = qs.toString();
  return apiFetch<CooccurrenceResponse>(`/skills/cooccurrence${query ? `?${query}` : ""}`);
}

export interface SkillCompanion {
  skill: string;
  display: string;
  joint_count: number;
  p_companion_given_skill: number;
  p_skill_given_companion: number;
  lift: number;
}

export interface SkillCompanionsResponse {
  skill: string;
  display: string;
  exclude_bulk: boolean;
  count: number;
  companions: SkillCompanion[];
}

export function getSkillCompanions(
  skill: string,
  params: { includeSoft?: boolean; excludeBulk?: boolean; limit?: number } = {}
) {
  const qs = new URLSearchParams();
  if (params.includeSoft) qs.set("include_soft", "true");
  if (params.excludeBulk) qs.set("exclude_bulk", "true");
  if (params.limit) qs.set("limit", String(params.limit));
  const query = qs.toString();
  return apiFetch<SkillCompanionsResponse>(
    `/skills/${encodeURIComponent(skill)}/companions${query ? `?${query}` : ""}`
  );
}

// ---------------------------------------------------------------------------
// POST /coverage
//
// Redesigned around "how many jobs am I a strong candidate for" (an
// absolute count) rather than a market-wide percentage. Percentages still
// exist, demoted to `stats` for footnote use only.
// ---------------------------------------------------------------------------

export interface CoverageMatchPosting {
  title: string | null;
  company: string | null;
  city: string | null;
  detail_url: string | null;
}

export interface CoverageStrongMatches {
  count: number;
  postings_shown: number;
  postings: CoverageMatchPosting[];
}

export interface CoverageFullMatches {
  count: number;
}

export interface CoverageDeltaEntry {
  skill: string;
  display: string;
  additional_strong_matches_if_added: number;
}

export interface CoverageStats {
  /** Already 0-100 (e.g. 0.7 means 0.7%) -- NOT a 0-1 fraction, unlike
   * formatPercent()'s expected input elsewhere. Render directly with a "%"
   * suffix; passing it through formatPercent would double-multiply. */
  strong_match_percent: number;
  full_match_percent: number;
}

export interface CoverageResponse {
  input_skills: string[];
  ignored_soft_skills: string[];
  exclude_bulk: boolean;
  total_postings_considered: number;
  strong_matches: CoverageStrongMatches;
  full_matches: CoverageFullMatches;
  delta_ranking: CoverageDeltaEntry[];
  stats: CoverageStats;
  note: string;
}

export async function postCoverage(
  skills: string[],
  params: { excludeBulk?: boolean } = {}
): Promise<CoverageResponse> {
  const qs = new URLSearchParams();
  if (params.excludeBulk) qs.set("exclude_bulk", "true");
  const query = qs.toString();
  const res = await fetch(`${API_URL}/coverage${query ? `?${query}` : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skills }),
  });
  if (!res.ok) {
    throw new Error(`Rukhwise API /coverage failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// /companies/top
// ---------------------------------------------------------------------------

export interface CompanySkill {
  skill: string;
  display: string;
  count: number;
}

export interface SalaryPKR {
  count: number;
  median: number | null;
  q1: number | null;
  q3: number | null;
  iqr: number | null;
}

export interface CompanyTemplated {
  tagged_count: number;
  templated_count: number;
  share: number | null;
}

export interface CompanySummary {
  company: string;
  posting_count: number;
  cities: string[];
  top_skills: CompanySkill[];
  salary_pkr: SalaryPKR;
  templated: CompanyTemplated;
}

export interface CompaniesTopResponse {
  count: number;
  companies: CompanySummary[];
}

export function getCompaniesTop(params: { includeSoft?: boolean; limit?: number } = {}) {
  const qs = new URLSearchParams();
  if (params.includeSoft) qs.set("include_soft", "true");
  if (params.limit) qs.set("limit", String(params.limit));
  const query = qs.toString();
  return apiFetch<CompaniesTopResponse>(`/companies/top${query ? `?${query}` : ""}`);
}

// ---------------------------------------------------------------------------
// /postings/foreign-currency
// ---------------------------------------------------------------------------

export interface ForeignCurrencyPosting {
  currency: string;
  salary_min: number | null;
  salary_max: number | null;
  title: string | null;
  company: string | null;
  city: string | null;
  detail_url: string | null;
  skills: string[];
}

export interface ForeignCurrencyBreakoutEntry {
  skill: string;
  display: string;
  count: number;
}

export interface ForeignCurrencyResponse {
  count: number;
  postings: ForeignCurrencyPosting[];
  breakout_stack: ForeignCurrencyBreakoutEntry[];
}

export function getPostingsForeignCurrency() {
  return apiFetch<ForeignCurrencyResponse>("/postings/foreign-currency");
}

// ---------------------------------------------------------------------------
// /system/health
// ---------------------------------------------------------------------------

export interface SystemHealth {
  last_successful_run_per_source: Record<string, string>;
  postings_added_24h: number;
  postings_added_7d: number;
  enrichment_coverage_mustakbil: number | null;
  extraction_coverage: number | null;
  data_freshness_hours: number | null;
  table_sizes: Record<string, number>;
  checked_at: string;
}

export function getSystemHealth() {
  return apiFetch<SystemHealth>("/system/health");
}

// ---------------------------------------------------------------------------
// /forecasts/pending
// ---------------------------------------------------------------------------

export type ForecastTargetType = "volume" | "skill";

export interface PendingForecast {
  target_type: ForecastTargetType;
  target_key: string;
  display: string;
  target_week_start: string;
  model_version: string;
  predicted: number;
  interval_low: number;
  interval_high: number;
  baseline_predicted: number;
  created_at: string;
  run_id: string;
  source_scope: string | null;
}

export interface ForecastsPendingResponse {
  count: number;
  forecasts: PendingForecast[];
}

export function getForecastsPending() {
  return apiFetch<ForecastsPendingResponse>("/forecasts/pending");
}

// ---------------------------------------------------------------------------
// /forecasts/accuracy
// ---------------------------------------------------------------------------

export interface GradedForecast {
  target_type: ForecastTargetType;
  target_key: string;
  display: string;
  target_week_start: string;
  model_version: string;
  predicted: number;
  baseline_predicted: number;
  actual: number;
  abs_error: number;
  baseline_abs_error: number;
  beat_baseline: boolean;
  pct_error: number | null;
  graded_at: string;
  source_scope: string | null;
}

export interface ForecastsAccuracySummary {
  count_graded: number;
  mae_overall: number | null;
  beat_baseline_rate_overall: number | null;
  beat_baseline_rate_by_type: Record<string, number>;
}

export interface ForecastsAccuracyResponse {
  forecasts: GradedForecast[];
  summary: ForecastsAccuracySummary;
}

export function getForecastsAccuracy() {
  return apiFetch<ForecastsAccuracyResponse>("/forecasts/accuracy");
}

// ---------------------------------------------------------------------------
// /backtest/summary & /backtest/detail
//
// A SEPARATE feature from /forecasts/pending and /forecasts/accuracy above --
// backtests are retrospective (computed after outcomes were already known),
// fully mutable, and re-computed from scratch on every `python backtest.py`
// run. Never merge these types/responses with the forecast ones; the /engine
// UI keeps them in a visually distinct section for the same reason.
// ---------------------------------------------------------------------------

export type BacktestOutcome = "beat" | "tie" | "lost";

export interface BacktestOutcomeCounts {
  n: number;
  beat: number;
  tie: number;
  lost: number;
  beat_rate: number | null;
  mae: number | null;
}

export interface BacktestTargetSummary extends BacktestOutcomeCounts {
  target_type: ForecastTargetType;
  target_key: string;
  display: string;
}

export interface BacktestSummaryResponse {
  n_weeks: number;
  n_rows: number;
  source_scope: string | null;
  overall: BacktestOutcomeCounts;
  by_target: BacktestTargetSummary[];
}

export function getBacktestSummary() {
  return apiFetch<BacktestSummaryResponse>("/backtest/summary");
}

export interface BacktestRow {
  target_type: ForecastTargetType;
  target_key: string;
  display: string;
  target_week_start: string;
  model_version: string;
  predicted: number;
  baseline_predicted: number;
  actual: number;
  abs_error: number;
  baseline_abs_error: number;
  outcome: BacktestOutcome;
  source_scope: string | null;
  computed_at: string;
}

export interface BacktestDetailResponse {
  count: number;
  backtests: BacktestRow[];
}

export function getBacktestDetail() {
  return apiFetch<BacktestDetailResponse>("/backtest/detail");
}
