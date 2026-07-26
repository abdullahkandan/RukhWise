import type { Insight, SkillTopEntry } from "./api";

// Mirrors api/main.py's SUBSTANTIVE_SKILL_EXCLUDED_CATEGORIES exactly --
// see that constant's comment for why this filter has to live in exactly
// one place per surface (backend already has its own copy; this is the
// frontend's, since a "top skill"/"market leader" stat computed client-
// side from /skills/top's raw, unfiltered rows needs the same definition
// of "a skill that belongs in an aggregate ranking" the backend insight
// generators use).
const MARKET_SKILL_EXCLUDED_CATEGORIES = new Set(["soft", "office_admin"]);

export function isMarketSkill(entry: Pick<SkillTopEntry, "category" | "requirement_type">): boolean {
  return entry.requirement_type === "skill" && !MARKET_SKILL_EXCLUDED_CATEGORIES.has(entry.category);
}

export type InsightTone = "alert" | "neutral";

export interface InsightDisplay {
  tone: InsightTone;
  bigNumber: string;
}

/**
 * The API returns a free-form `value` object per insight (no shared
 * schema across the 6 generators by design). This maps each generator's
 * distinctive value keys to a display number + tone: red for movers/
 * alerts (top_mover, foreign_currency), cerulean for neutral analytical
 * facts (everything else) -- matches the brief's color rule directly.
 */
export function classifyInsight(insight: Insight): InsightDisplay {
  const v = insight.value;

  if (typeof v.delta === "number") {
    const delta = v.delta as number;
    return { tone: "alert", bigNumber: `${delta > 0 ? "+" : ""}${delta}` };
  }
  if (typeof v.by_currency === "object" && v.by_currency !== null) {
    return { tone: "alert", bigNumber: String(v.count ?? "") };
  }
  if (typeof v.templated_postings === "number") {
    return { tone: "neutral", bigNumber: `${Math.round((v.share as number) * 100)}%` };
  }
  if (typeof v.overall_median_days === "number") {
    const days = v.overall_median_days as number;
    return { tone: "neutral", bigNumber: `${days.toFixed(days < 10 ? 1 : 0)}d` };
  }
  if (typeof v.ratio === "number") {
    return { tone: "neutral", bigNumber: `${(v.ratio as number).toFixed(1)}×` };
  }
  if (typeof v.zero_technical_count === "number") {
    return { tone: "neutral", bigNumber: `${Math.round((v.share as number) * 100)}%` };
  }
  if (typeof v.joint_count === "number" && typeof v.lift === "number") {
    // Strongest-pairing insight -- leads with the plain count here too,
    // matching the phrasing discipline applied to its headline/detail text
    // (lift alone reads as an alarming multiplier for small samples).
    return { tone: "neutral", bigNumber: String(v.joint_count) };
  }
  if (typeof v.p_companion_given_skill === "number") {
    return { tone: "neutral", bigNumber: `${Math.round((v.p_companion_given_skill as number) * 100)}%` };
  }
  if (typeof v.company === "string" && typeof v.posting_count === "number") {
    return { tone: "alert", bigNumber: String(v.posting_count) };
  }

  return { tone: "neutral", bigNumber: "" };
}

/**
 * "Researcher-flavored" insights -- about the data/methodology itself
 * (templated listings, foreign-currency pricing) rather than the market --
 * live on /engine under "Findings for the curious" instead of Home's
 * general-interest teaser.
 */
export function isResearcherInsight(insight: Insight): boolean {
  const v = insight.value;
  return (
    typeof v.templated_postings === "number" ||
    (typeof v.by_currency === "object" && v.by_currency !== null)
  );
}
