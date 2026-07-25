"use client";

import { useMemo, useState, useTransition } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  postCoverage,
  postPathsMatch,
  type CoverageResponse,
  type PathsMatchResponse,
  type SkillTopEntry,
} from "@/lib/api";
import { CATEGORY_LABELS, categoryLabel } from "@/lib/categories";

interface SkillGapPickerProps {
  skills: SkillTopEntry[];
}

const CATEGORY_ORDER = Object.keys(CATEGORY_LABELS).filter((k) => k !== "soft");

export function SkillGapPicker({ skills }: SkillGapPickerProps) {
  // Soft skills are excluded from the picker entirely -- "match strength" is
  // a technical-demand question, and the API silently drops any soft entry
  // from the working set anyway.
  const technical = useMemo(() => skills.filter((s) => s.category !== "soft"), [skills]);

  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<CoverageResponse | null>(null);
  const [pathsMatch, setPathsMatch] = useState<PathsMatchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pool = q ? technical.filter((s) => s.display.toLowerCase().includes(q)) : technical;
    const map: [string, SkillTopEntry[]][] = [];
    for (const cat of CATEGORY_ORDER) {
      const items = pool.filter((s) => s.category === cat);
      if (items.length > 0) map.push([cat, items]);
    }
    return map;
  }, [technical, query]);

  const selectedEntries = technical.filter((s) => selected.has(s.skill));

  function toggle(skill: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(skill)) next.delete(skill);
      else next.add(skill);
      return next;
    });
    setResult(null); // selection changed -- stale results shouldn't linger
    setPathsMatch(null);
  }

  function analyze() {
    setError(null);
    startTransition(async () => {
      try {
        const skills = [...selected];
        const res = await postCoverage(skills);
        setResult(res);
        // Secondary, subordinate to the coverage result above -- a failure
        // here shouldn't block or error out the main strong-match flow, so
        // it's a silent best-effort fetch, not part of the try/catch below.
        postPathsMatch(skills)
          .then(setPathsMatch)
          .catch(() => setPathsMatch(null));
      } catch {
        setError("Couldn't analyze right now — try again in a moment.");
      }
    });
  }

  return (
    <div>
      <label className="font-mono text-xs uppercase tracking-[0.16em] text-java/50">
        What can you do?
      </label>

      {selectedEntries.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {selectedEntries.map((s) => (
            <button
              key={s.skill}
              type="button"
              onClick={() => toggle(s.skill)}
              className="flex items-center gap-1.5 border border-java bg-java px-3 py-1.5 font-sans text-sm text-cream transition-colors duration-150 cursor-pointer hover:bg-sceptre hover:border-sceptre"
            >
              {s.display}
              <span aria-hidden className="text-cream/60">
                ×
              </span>
            </button>
          ))}
        </div>
      )}

      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search skills…"
        className="mt-4 min-h-[44px] w-full border border-java/30 bg-transparent px-3 py-2 font-mono text-sm text-java outline-none placeholder:text-java/40 focus:border-java"
      />

      <div className="mt-5 max-h-80 overflow-y-auto border border-java/15 p-4">
        {grouped.map(([cat, items]) => (
          <div key={cat} className="mb-5 last:mb-0">
            <p className="font-mono text-xs uppercase tracking-widest text-java/40">
              {categoryLabel(cat)}
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {items.map((s) => {
                const active = selected.has(s.skill);
                return (
                  <button
                    key={s.skill}
                    type="button"
                    aria-pressed={active}
                    onClick={() => toggle(s.skill)}
                    className={`min-h-[38px] rounded-full border px-3 py-1.5 font-sans text-sm transition-colors duration-150 cursor-pointer ${
                      active
                        ? "border-java bg-java text-cream"
                        : "border-java/30 bg-transparent text-java hover:border-java"
                    }`}
                  >
                    {s.display}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        {grouped.length === 0 && (
          <p className="font-sans text-sm text-java/50">No skills match &ldquo;{query}&rdquo;.</p>
        )}
      </div>

      <button
        type="button"
        onClick={analyze}
        disabled={selected.size === 0 || isPending}
        className="mt-6 min-h-[48px] w-full border border-java bg-java px-6 font-mono text-sm uppercase tracking-[0.16em] text-cream transition-opacity duration-150 disabled:opacity-40 cursor-pointer sm:w-auto sm:min-w-[240px]"
      >
        {isPending ? "Analyzing…" : "Analyze my coverage"}
      </button>
      {error && <p className="mt-3 font-mono text-xs text-sceptre-bright">{error}</p>}

      {!result && (
        <p className="mt-10 font-sans text-sm text-java/50">
          Pick what you know, and see how many jobs you&rsquo;re a strong candidate for right now.
        </p>
      )}

      <AnimatePresence>
        {result && (
          <motion.div
            key="results"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-14 border-t border-java/15 pt-12">
              <div>
                <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">
                  You&rsquo;re a strong match for
                </p>
                <p className="mt-3 font-mono text-6xl font-semibold leading-none tabular-nums text-sceptre-bright sm:text-7xl md:text-8xl">
                  {result.strong_matches.count}
                </p>
                <p className="mt-2 max-w-md font-sans text-sm text-java/60">
                  {result.strong_matches.count === 1 ? "posting" : "postings"} right now — where your
                  skills cover at least 70% of what&rsquo;s asked for.
                </p>
              </div>

              {result.strong_matches.postings.length > 0 && (
                <div className="mt-10 flex flex-col gap-3">
                  {result.strong_matches.postings.slice(0, 8).map((p, i) => (
                    <motion.a
                      key={`${p.title}-${i}`}
                      href={p.detail_url ?? "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.3, delay: 0.1 + i * 0.05, ease: [0.16, 1, 0.3, 1] }}
                      className="-mx-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded-sm border-b border-java/10 px-2 py-2.5 transition-colors duration-150 hover:border-java/30 hover:bg-java/5"
                    >
                      <span className="font-sans text-sm font-medium text-java">{p.title}</span>
                      <span className="font-mono text-xs text-java/50">
                        {[p.company, p.city].filter(Boolean).join(" — ")}
                      </span>
                    </motion.a>
                  ))}
                  {result.strong_matches.count > result.strong_matches.postings.slice(0, 8).length && (
                    <p className="mt-1 font-mono text-xs text-java/40">
                      +{result.strong_matches.count - result.strong_matches.postings.slice(0, 8).length}{" "}
                      more not shown
                    </p>
                  )}
                </div>
              )}

              <div className="mt-8 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-mono text-2xl font-semibold tabular-nums text-java">
                  {result.full_matches.count}
                </span>
                <span className="max-w-sm font-sans text-sm text-java/60">
                  fully covered — every technical skill asked for, not just most of it.
                </span>
              </div>

              {result.delta_ranking.length > 0 && (
                <div className="mt-14">
                  <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">
                    Learn this next
                  </p>
                  <ol className="mt-6 flex flex-col gap-5">
                    {result.delta_ranking.map((d, i) => (
                      <motion.li
                        key={d.skill}
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.3, delay: 0.15 + i * 0.05, ease: [0.16, 1, 0.3, 1] }}
                        className="flex flex-col gap-1"
                      >
                        <div className="flex items-baseline gap-3">
                          <span
                            className={`font-mono text-sm ${i === 0 ? "text-sceptre-bright" : "text-java/40"}`}
                          >
                            {String(i + 1).padStart(2, "0")}
                          </span>
                          <span
                            className={`font-sans text-base font-medium ${
                              i === 0 ? "text-sceptre-bright" : "text-java"
                            }`}
                          >
                            {d.display}
                          </span>
                        </div>
                        <span
                          className={`ml-9 font-mono text-sm tabular-nums ${
                            i === 0 ? "text-sceptre-bright" : "text-java/60"
                          }`}
                        >
                          become a strong match for +{d.additional_strong_matches_if_added} more postings
                        </span>
                      </motion.li>
                    ))}
                  </ol>
                </div>
              )}

              <p className="mt-10 font-mono text-xs text-java/40">
                {result.stats.strong_match_percent}% of technical postings, {result.stats.full_match_percent}%
                fully.
              </p>
              <p className="mt-3 font-mono text-xs leading-relaxed text-java/50">{result.note}</p>

              {/* Secondary, subordinate panel -- only appears when the user's
                  picks map strongly (>=70% of a level's core skills) onto a
                  job_family/experience_level. Deliberately smaller and set
                  off by its own divider so it never competes with the
                  strong-match result above. */}
              {pathsMatch?.matched && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
                  className="mt-10 border-t border-java/10 pt-8"
                >
                  <p className="font-mono text-xs uppercase tracking-[0.16em] text-java/40">
                    Your picks read as {pathsMatch.display} ({pathsMatch.your_level})
                  </p>
                  {pathsMatch.next_level ? (
                    <>
                      <p className="mt-2 max-w-md font-sans text-sm text-java/60">
                        At the <strong className="font-medium text-java">{pathsMatch.next_level}</strong> level,
                        these appear that don&rsquo;t at yours:
                      </p>
                      <ul className="mt-4 flex flex-col gap-2">
                        {pathsMatch.next_level_skills.slice(0, 6).map((s) => (
                          <li key={s.skill} className="flex items-baseline gap-3">
                            <span className="font-sans text-sm text-java">{s.display}</span>
                            <span className="font-mono text-xs tabular-nums text-java/40">
                              +{Math.round(s.company_share_delta * 100)}pt company share
                            </span>
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : (
                    <p className="mt-2 max-w-md font-sans text-sm text-java/60">
                      That&rsquo;s the highest level we have enough data to describe for this family --
                      no further delta to show.
                    </p>
                  )}
                  <p className="mt-4 font-mono text-xs leading-relaxed text-java/40">
                    {pathsMatch.honest_constraint}
                  </p>
                </motion.div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
