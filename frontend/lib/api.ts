/**
 * All Supabase-backed data comes through here, server-side. Every call
 * revalidates on a 10-minute window (matches the API's own 10-min
 * in-process cache — the collection pipeline runs daily, so this is
 * generous headroom, not a freshness risk). Zero mock data: every function
 * here hits the live FastAPI service.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const REVALIDATE_SECONDS = 600;

/**
 * Returns null (never throws) on any failure -- network error, timeout, or
 * a non-2xx response. This runs at BUILD time for every page that fetches
 * server-side (ISR prerendering), so a stale or momentarily-down API must
 * never take the whole Vercel build down with it (see e.g. the
 * /curriculum/alignment 404 that did exactly that). Every caller must
 * handle a null result -- an honest "temporarily unavailable" empty state,
 * never fabricated zeros. Errors are still logged so a real outage is
 * visible in build/server logs, just not fatal to them.
 */
async function apiFetch<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      next: { revalidate: REVALIDATE_SECONDS },
    });
    if (!res.ok) {
      console.error(`Rukhwise API ${path} failed: ${res.status} ${res.statusText}`);
      return null;
    }
    return (await res.json()) as T;
  } catch (err) {
    console.error(`Rukhwise API ${path} failed:`, err);
    return null;
  }
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
  /** 'skill' | 'attribute' | 'language' -- 'attribute' covers
   * work_arrangement entries (On-Site, Full-Time, Morning Shift, ...),
   * which are not skills. A "top skill" claim must filter to
   * requirement_type === 'skill' in addition to excluding soft/
   * office_admin categories -- see isMarketSkill in lib/insights.ts. */
  requirement_type: string;
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
  /** 'market' (a finding for someone looking for work -- Home) or
   * 'system' (a finding about the pipeline/data itself -- /engine, where
   * pipeline self-monitoring already lives). Set explicitly by every
   * generator on the backend, not inferred client-side. */
  audience: "market" | "system";
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
  /** True for a batch predicted before forecast.py's AUTOMATED_COLLECTION_START
   * fix, whose trailing-mean history included Mustakbil's one-time bulk
   * backfill week -- see api/main.py's COLLECTION_REGIME_CONTAMINATED_WEEKS. */
  collection_regime_note: boolean;
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
  /** Derived server-side from abs_error vs baseline_abs_error -- the
   * honest three-way split. beat_baseline (boolean, above) collapses a
   * tie into "false", which reads as a loss; use this field for display. */
  outcome: "beat" | "tie" | "lost";
  pct_error: number | null;
  graded_at: string;
  source_scope: string | null;
  /** Same annotation PendingForecast carries -- persists once a
   * contaminated batch is graded, since "their errors reflect that, not
   * forecast skill" applies just as much after grading as before. */
  collection_regime_note: boolean;
}

export interface ForecastOutcomeCounts {
  beat: number;
  tie: number;
  lost: number;
}

export interface ForecastsAccuracySummary {
  count_graded: number;
  mae_overall: number | null;
  beat_baseline_rate_overall: number | null;
  beat_baseline_rate_by_type: Record<string, number>;
  outcome_counts_overall: ForecastOutcomeCounts;
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

// ---------------------------------------------------------------------------
// /curriculum/alignment & /curriculum/gaps
//
// SCOPE LIMITATION: both source curricula (NCEAC BS Computing Disciplines
// 2023, HEC Computer Science 2025) cover COMPUTING disciplines only. This
// compares computing education against computing-sector market demand
// (technology_it/engineering postings) -- nothing else on the site.
// ---------------------------------------------------------------------------

export interface CurriculumSkillEntry {
  skill: string;
  display: string;
  category: string;
  posting_count: number;
  company_count: number;
  course_count?: number;
}

export interface CurriculumAlignmentResponse {
  scope_note: string;
  matching_note: string;
  market_domains: string[];
  market_postings_considered: number;
  courses_total: number;
  courses_matched: number;
  courses_unmatched: number;
  taught_and_demanded: CurriculumSkillEntry[];
  demanded_not_taught: CurriculumSkillEntry[];
  demanded_not_taught_note: string;
}

export function getCurriculumAlignment() {
  return apiFetch<CurriculumAlignmentResponse>("/curriculum/alignment");
}

export interface CurriculumGapsResponse {
  scope_note: string;
  matching_note: string;
  market_domains: string[];
  min_companies_threshold: number;
  count: number;
  gaps: CurriculumSkillEntry[];
}

export function getCurriculumGaps() {
  return apiFetch<CurriculumGapsResponse>("/curriculum/gaps");
}

// ---------------------------------------------------------------------------
// /paths/{family}, /paths/match -- skill adjacency by seniority within a
// job_family. HONEST CONSTRAINT, carried through every response: job
// postings never show the same person twice, so career transitions cannot
// be observed -- this infers what employers ask for at each level, and the
// delta between levels, not observed career movement.
// ---------------------------------------------------------------------------

export interface PathSkillEntry {
  skill: string;
  display: string;
  category: string;
  company_count: number;
  posting_count: number;
}

export interface PathLevel {
  level: string;
  n_postings: number;
  n_companies: number;
  skills: PathSkillEntry[];
}

export interface PathDeltaSkillEntry {
  skill: string;
  display: string;
  category: string;
  company_share_lower: number;
  company_share_higher: number;
  company_share_delta: number;
  company_count_higher: number;
}

export interface PathDelta {
  from_level: string;
  to_level: string;
  n_lower: { postings: number; companies: number };
  n_higher: { postings: number; companies: number };
  skills: PathDeltaSkillEntry[];
}

export interface PathForFamilyResponse {
  family: string;
  display: string;
  has_data: boolean;
  n_postings: number;
  levels_present: string[];
  reason?: string;
  levels?: PathLevel[];
  deltas?: PathDelta[];
  honest_constraint: string;
  min_postings_threshold: number;
  min_levels_threshold: number;
}

export async function getPathsForFamily(family: string): Promise<PathForFamilyResponse | null> {
  return apiFetch<PathForFamilyResponse>(`/paths/${encodeURIComponent(family)}`);
}

export interface PathsMatchResponse {
  honest_constraint: string;
  match_threshold: number;
  matched: boolean;
  family: string | null;
  display: string | null;
  your_level: string | null;
  match_fraction: number | null;
  next_level: string | null;
  next_level_skills: PathDeltaSkillEntry[];
}

export async function postPathsMatch(skills: string[]): Promise<PathsMatchResponse> {
  const res = await fetch(`${API_URL}/paths/match`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skills }),
  });
  if (!res.ok) {
    throw new Error(`Rukhwise API /paths/match failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// /briefings/latest, /briefings -- the fully-automated weekly briefing
// (briefing.py). source is 'llm' (drafted by Groq, then machine-verified
// against its own facts before publication) or 'template' (the verifier
// blocked the draft, or Groq wasn't reachable -- a plain briefing built
// directly from the same facts instead). Both are equally true; neither is
// a "degraded" version of the other.
// ---------------------------------------------------------------------------

export interface BriefingSummary {
  id: string;
  week_start: string;
  created_at: string;
  body: string;
  source: "llm" | "template";
  model_version: string | null;
  blocked_reason: string | null;
  /** Set once this row has been corrected by a later briefing for the
   * same week -- points at the replacement's id. The row itself is never
   * edited; this is the only field that ever changes after publish. */
  superseded_by: string | null;
}

export interface BriefingsLatestResponse {
  has_briefing: boolean;
  id?: string;
  week_start?: string;
  created_at?: string;
  body?: string;
  source?: "llm" | "template";
  model_version?: string | null;
  blocked_reason?: string | null;
  superseded_by?: string | null;
  /** Present only when the current briefing is itself a correction --
   * the full original it replaced, for plain on-page disclosure. */
  supersedes?: BriefingSummary | null;
}

export async function getBriefingsLatest(): Promise<BriefingsLatestResponse | null> {
  return apiFetch<BriefingsLatestResponse>("/briefings/latest");
}

export interface BriefingsListResponse {
  count: number;
  briefings: BriefingSummary[];
}

export async function getBriefings(limit?: number): Promise<BriefingsListResponse | null> {
  const query = limit ? `?limit=${limit}` : "";
  return apiFetch<BriefingsListResponse>(`/briefings${query}`);
}
