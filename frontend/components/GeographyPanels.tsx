"use client";

import { useState, useTransition } from "react";
import {
  getCitiesBreakdown,
  getSalariesSummary,
  type CitiesBreakdownResponse,
  type SalariesSummaryResponse,
  type SkillTopEntry,
} from "@/lib/api";
import { Combobox } from "./Combobox";
import { CitiesList } from "./CitiesList";
import { SalaryBands } from "./SalaryBands";
import { ScrollReveal } from "./ScrollReveal";

interface GeographyPanelsProps {
  skills: SkillTopEntry[];
  initialCities: CitiesBreakdownResponse;
  initialSalaries: SalariesSummaryResponse;
  foreignCurrencyCount: number;
}

/**
 * One filter, two panels. The skill selector drives the city ranking and the
 * salary bands together — a single refetch updates both, so the control's
 * scope is unambiguous rather than looking like it only governs the list it
 * sits nearest.
 */
export function GeographyPanels({
  skills,
  initialCities,
  initialSalaries,
  foreignCurrencyCount,
}: GeographyPanelsProps) {
  const [skill, setSkill] = useState("");
  const [cities, setCities] = useState(initialCities);
  const [salaries, setSalaries] = useState(initialSalaries);
  const [isPending, startTransition] = useTransition();

  const options = [
    { value: "", label: "All skills" },
    ...skills.map((s) => ({ value: s.skill, label: s.display })),
  ];
  const skillLabel = skills.find((s) => s.skill === skill)?.display;

  function handleChange(value: string) {
    setSkill(value);
    startTransition(async () => {
      // apiFetch never throws -- a failed fetch returns null, checked
      // explicitly below. Keep last good data rather than blanking both
      // panels on a transient failure.
      const [freshCities, freshSalaries] = await Promise.all([
        getCitiesBreakdown(value || undefined),
        getSalariesSummary("PKR", value || undefined),
      ]);
      if (freshCities) setCities(freshCities);
      if (freshSalaries) setSalaries(freshSalaries);
    });
  }

  return (
    <div className="mt-12">
      <div className="flex flex-col gap-3 border-t border-cream/15 pt-6 sm:flex-row sm:items-center sm:justify-between">
        <p className="font-mono text-xs uppercase tracking-[0.16em] text-cream/50">
          Filter both panels by skill
        </p>
        <Combobox
          options={options}
          value={skill}
          onChange={handleChange}
          ariaLabel="Filter cities and salaries by skill"
          placeholder="All skills"
          variant="java"
          className="w-full sm:w-64"
        />
      </div>

      <div className="mt-14 grid grid-cols-1 gap-16 lg:grid-cols-2 lg:gap-12">
        <ScrollReveal index={0} staggerMs={80}>
          <CitiesList data={cities} isPending={isPending} />
        </ScrollReveal>
        <ScrollReveal index={1} staggerMs={80}>
          <SalaryBands
            data={salaries}
            skillLabel={skillLabel}
            foreignCurrencyCount={foreignCurrencyCount}
            isPending={isPending}
          />
        </ScrollReveal>
      </div>
    </div>
  );
}
