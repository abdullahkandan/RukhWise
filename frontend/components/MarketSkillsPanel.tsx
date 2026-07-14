"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { motion, useInView, useReducedMotion } from "framer-motion";
import {
  getSkillCompanions,
  getSkillsCooccurrence,
  getSkillsTop,
  type CooccurrencePair,
  type SkillCompanionsResponse,
  type SkillTopEntry,
} from "@/lib/api";
import { CATEGORY_LABELS, categoryLabel } from "@/lib/categories";
import { formatPercent } from "@/lib/format";
import { Pill } from "./Pill";
import { Combobox } from "./Combobox";

interface MarketSkillsPanelProps {
  initialSkills: SkillTopEntry[];
  initialTotalPostings: number;
  topCompanyShare: number;
  initialCooccurrence: CooccurrencePair[];
  defaultBundleSkill: string;
  initialCompanions: SkillCompanionsResponse;
}

const CATEGORY_ORDER = Object.keys(CATEGORY_LABELS).filter((k) => k !== "soft");
const BULK_THRESHOLD = 0.25;

function EditorialToggle({
  on,
  onToggle,
  label,
}: {
  on: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={label}
        onClick={onToggle}
        className={`relative h-5 w-9 shrink-0 rounded-full border transition-colors duration-150 cursor-pointer ${
          on ? "border-sceptre bg-sceptre" : "border-cream/40 bg-transparent hover:border-cream/70"
        }`}
      >
        <span
          className={`absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full transition-all duration-200 ${
            on ? "left-[18px] bg-cream" : "left-0.5 bg-cream/45"
          }`}
        />
      </button>
      <span className="font-mono text-xs uppercase tracking-[0.16em] text-cream/60">{label}</span>
    </div>
  );
}

/**
 * Owns the one piece of state that spans the market page's reactive
 * subsections -- exclude_bulk -- since /cities/breakdown and /salaries/
 * summary never got that param (Phase 1 scoped it to /skills/top,
 * /skills/cooccurrence, /skills/{skill}/companions, /coverage only), so
 * Geography+Money and Compare stay separate, unaffected server sections.
 */
export function MarketSkillsPanel({
  initialSkills,
  initialTotalPostings,
  topCompanyShare,
  initialCooccurrence,
  defaultBundleSkill,
  initialCompanions,
}: MarketSkillsPanelProps) {
  const [excludeBulk, setExcludeBulk] = useState(false);
  const [metric, setMetric] = useState<"postings" | "companies">("postings");
  const [category, setCategory] = useState<string | null>(null);
  const [includeSoft, setIncludeSoft] = useState(false);

  const [skills, setSkills] = useState(initialSkills);
  const [totalPostings, setTotalPostings] = useState(initialTotalPostings);
  const [cooccurrence, setCooccurrence] = useState(initialCooccurrence);
  const [bundleSkill, setBundleSkill] = useState(defaultBundleSkill);
  const [companions, setCompanions] = useState(initialCompanions);
  const [isPending, startTransition] = useTransition();
  const [isBundlePending, startBundleTransition] = useTransition();

  const barsRef = useRef<HTMLDivElement>(null);
  const barsInView = useInView(barsRef, { once: true, amount: 0.2 });
  const reduce = useReducedMotion();
  const shown = barsInView || reduce;

  // exclude_bulk changes the underlying corpus (not just a client-side
  // filter), so it re-fetches the two datasets it governs together.
  useEffect(() => {
    startTransition(async () => {
      try {
        const [freshSkills, freshCooc] = await Promise.all([
          getSkillsTop({ includeSoft: true, excludeBulk, limit: 100 }),
          getSkillsCooccurrence({ excludeBulk, limit: 5 }),
        ]);
        setSkills(freshSkills.skills);
        setTotalPostings(freshSkills.total_postings);
        setCooccurrence(freshCooc.pairs);
        const freshCompanions = await getSkillCompanions(bundleSkill, { excludeBulk });
        setCompanions(freshCompanions);
      } catch {
        // Keep the last good data rather than a broken panel.
      }
    });
    // bundleSkill intentionally excluded -- handled by its own effect below,
    // this one only fires on the exclude_bulk toggle itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [excludeBulk]);

  function handleBundleSkillChange(value: string) {
    setBundleSkill(value);
    startBundleTransition(async () => {
      try {
        const fresh = await getSkillCompanions(value, { excludeBulk });
        setCompanions(fresh);
      } catch {
        // Keep last good data.
      }
    });
  }

  const presentCategories = useMemo(() => {
    const present = new Set(skills.map((s) => s.category));
    return CATEGORY_ORDER.filter((c) => present.has(c));
  }, [skills]);

  const technical = useMemo(() => skills.filter((s) => s.category !== "soft"), [skills]);

  const filtered = useMemo(() => {
    return skills
      .filter((s) => (includeSoft ? true : s.category !== "soft"))
      .filter((s) => (category ? s.category === category : true))
      .sort((a, b) =>
        metric === "postings" ? b.posting_count - a.posting_count : b.company_count - a.company_count
      )
      .slice(0, 20);
  }, [skills, category, includeSoft, metric]);

  const maxValue =
    filtered.length > 0
      ? metric === "postings"
        ? filtered[0].posting_count
        : filtered[0].company_count
      : 1;

  const bundleOptions = technical.map((s) => ({ value: s.skill, label: s.display }));
  const maxCompanionP = companions.companions[0]?.p_companion_given_skill ?? 1;

  return (
    <div>
      {/* Concentration disclosure + the one control that governs everything
          reactive on this page. */}
      {topCompanyShare > BULK_THRESHOLD && (
        <div className="mb-10 border border-cream/20 bg-soil px-6 py-5">
          <p className="font-sans text-sm leading-relaxed text-cream/80">
            The single highest-volume employer holds{" "}
            <strong className="text-cream">{formatPercent(topCompanyShare, 0)}</strong> of all
            tracked postings — enough to shape skill rankings and pairings on its own.
          </p>
          <div className="mt-4">
            <EditorialToggle
              on={excludeBulk}
              onToggle={() => setExcludeBulk((v) => !v)}
              label="Exclude bulk posters (&gt;25% share)"
            />
          </div>
        </div>
      )}

      {/* --- Skill ranking --- */}
      <div className={`transition-opacity duration-200 ${isPending ? "opacity-50" : "opacity-100"}`}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <Pill
              label="All"
              active={category === null}
              onClick={() => setCategory(null)}
              variant="java"
            />
            {presentCategories.map((c) => (
              <Pill
                key={c}
                label={categoryLabel(c)}
                active={category === c}
                onClick={() => setCategory(c)}
                variant="java"
              />
            ))}
          </div>
          <EditorialToggle on={includeSoft} onToggle={() => setIncludeSoft((v) => !v)} label="Soft skills" />
        </div>

        <div className="mt-4 flex items-center gap-2">
          <button
            type="button"
            onClick={() => setMetric("postings")}
            className={`font-mono text-xs uppercase tracking-[0.14em] transition-colors duration-150 cursor-pointer ${
              metric === "postings" ? "text-sceptre-bright" : "text-cream/40 hover:text-cream/70"
            }`}
          >
            By postings
          </button>
          <span className="text-cream/30">/</span>
          <button
            type="button"
            onClick={() => setMetric("companies")}
            className={`font-mono text-xs uppercase tracking-[0.14em] transition-colors duration-150 cursor-pointer ${
              metric === "companies" ? "text-sceptre-bright" : "text-cream/40 hover:text-cream/70"
            }`}
          >
            By companies
          </button>
        </div>

        {!includeSoft && (
          <p className="mt-4 font-sans text-sm text-cream/50">
            Soft skills hidden — they&rsquo;re everywhere and say little.
          </p>
        )}

        <div ref={barsRef} className="mt-10 flex flex-col gap-3">
          {filtered.map((skill, i) => {
            const isTop3 = i < 3;
            const value = metric === "postings" ? skill.posting_count : skill.company_count;
            const widthPct = Math.max(4, (value / maxValue) * 100);
            return (
              <motion.div
                key={skill.skill}
                layout
                transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4"
              >
                <div className="flex items-center gap-3">
                  <span className="w-36 shrink-0 truncate font-sans text-sm font-medium sm:w-44">
                    {skill.display}
                  </span>
                  <div className="h-6 flex-1 bg-cream/10">
                    <motion.div
                      layout
                      className={`h-full ${isTop3 ? "bg-sceptre-bright" : "bg-cerulean"}`}
                      initial={false}
                      animate={{ width: shown ? `${widthPct}%` : 0 }}
                      transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
                    />
                  </div>
                </div>
                <span className="font-mono tabular-nums text-sm text-cream/80">{value}</span>
              </motion.div>
            );
          })}
        </div>

        <p className="mt-8 font-mono text-xs text-cream/50">
          {metric === "postings"
            ? `Share of ${totalPostings.toLocaleString("en-US")} tracked postings mentioning each skill at least once.`
            : "Distinct companies whose postings mention each skill at least once."}
        </p>
      </div>

      {/* --- Bundles: companions + strongest pairings --- */}
      <div className="mt-20 border-t border-cream/15 pt-16">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-cream/50">Skill bundles</p>
        <h3 className="mt-3 max-w-xl font-display text-2xl font-medium leading-tight md:text-3xl">
          What goes together
        </h3>

        <div className="mt-10">
          <Combobox
            options={bundleOptions}
            value={bundleSkill}
            onChange={handleBundleSkillChange}
            ariaLabel="Choose a skill to see its companions"
            variant="java"
            className="w-full sm:w-64"
          />

          <p className="mt-8 font-sans text-sm text-cream/60">
            Employers asking for{" "}
            <span className="font-medium text-cream">{companions.display}</span> also ask for:
          </p>

          <div
            className={`mt-6 flex flex-col gap-6 transition-opacity duration-200 ${
              isBundlePending ? "opacity-50" : "opacity-100"
            }`}
          >
            {companions.companions.length === 0 && (
              <p className="font-sans text-sm text-cream/50">
                Not enough co-occurrence data for {companions.display} yet.
              </p>
            )}
            {companions.companions.map((c, i) => (
              <div key={c.skill}>
                <div className="flex items-baseline justify-between gap-4">
                  <span
                    className={`font-sans text-base font-medium ${i === 0 ? "text-sceptre-bright" : "text-cream"}`}
                  >
                    {c.display}
                  </span>
                  <div className="flex items-baseline gap-3">
                    <span
                      className={`font-mono text-2xl font-semibold tabular-nums ${
                        i === 0 ? "text-sceptre-bright" : "text-cerulean"
                      }`}
                    >
                      {formatPercent(c.p_companion_given_skill)}
                    </span>
                    <span className="font-mono text-xs text-cream/40">
                      together in {c.joint_count.toLocaleString("en-US")} postings
                    </span>
                  </div>
                </div>
                <div className="mt-2 h-1.5 bg-cream/10">
                  <div
                    className={`h-full ${i === 0 ? "bg-sceptre-bright" : "bg-cerulean"}`}
                    style={{ width: `${Math.max(4, (c.p_companion_given_skill / maxCompanionP) * 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        {cooccurrence.length > 0 && (
          <div className="mt-16 border-t border-cream/15 pt-10">
            <p className="font-mono text-xs uppercase tracking-[0.16em] text-cream/50">
              Strongest pairings
            </p>
            <div className="mt-5 flex flex-wrap gap-3">
              {cooccurrence.map((p) => (
                <div
                  key={`${p.skill_a}-${p.skill_b}`}
                  className="flex items-center gap-2 border border-cream/15 px-4 py-2"
                >
                  <span className="font-sans text-sm">
                    {p.display_a} <span className="text-cream/40">+</span> {p.display_b}
                  </span>
                  <span className="font-mono text-xs text-cream/50">{p.joint_count}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <p className="mt-10 font-mono text-xs text-cream/50">
          Co-occurrence reflects the current tracked corpus; a single high-volume employer can
          dominate pairings, see{" "}
          <a href="/companies" className="underline underline-offset-2 hover:text-cream">
            Companies
          </a>
          .
        </p>
      </div>
    </div>
  );
}
