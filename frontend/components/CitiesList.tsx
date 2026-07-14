import type { CitiesBreakdownResponse } from "@/lib/api";
import { CountUp } from "./CountUp";

interface CitiesListProps {
  data: CitiesBreakdownResponse;
  isPending?: boolean;
}

/**
 * Presentational ranked-city list. The skill filter that drives it lives one
 * level up in GeographyPanels, so this component just renders whatever data
 * it's handed — cities and salaries move together under a single control.
 */
export function CitiesList({ data, isPending = false }: CitiesListProps) {
  // "All Cities" is Rozee's placeholder for multi-city postings, not a real
  // place — excluded from the ranking and surfaced as a footnote instead.
  const rankedCities = data.cities.filter((c) => c.city.trim().toLowerCase() !== "all cities");
  const allCitiesCount = data.cities.find((c) => c.city.trim().toLowerCase() === "all cities")?.count ?? 0;
  const top = rankedCities.slice(0, 10);
  const maxCount = top.length > 0 ? top[0].count : 1;

  return (
    <div>
      <h3 className="font-display text-2xl font-medium leading-tight md:text-3xl">
        Where the postings are
      </h3>

      <ol
        className={`mt-8 flex flex-col gap-4 transition-opacity duration-200 ${
          isPending ? "opacity-50" : "opacity-100"
        }`}
      >
        {top.map((city, i) => (
          <li
            key={city.city}
            className="grid grid-cols-[2rem_1fr_auto] items-center gap-4 rounded-sm px-2 py-1 -mx-2 transition-colors duration-150 hover:bg-cream/5"
          >
            <span className="font-mono text-sm text-cream/40">{String(i + 1).padStart(2, "0")}</span>
            <div>
              <p className="font-sans text-sm font-medium">{city.city}</p>
              <div className="mt-1.5 h-1 bg-cream/10">
                <div
                  className="h-full bg-cerulean"
                  style={{ width: `${Math.max(4, (city.count / maxCount) * 100)}%` }}
                />
              </div>
            </div>
            <CountUp value={city.count} className="font-mono tabular-nums text-sm text-cream/80" />
          </li>
        ))}
        {top.length === 0 && (
          <li className="font-sans text-sm text-cream/50">No cities for this skill yet.</li>
        )}
      </ol>

      {allCitiesCount > 0 && (
        <p className="mt-6 font-mono text-xs text-cream/50">
          +{allCitiesCount} postings listed across all cities.
        </p>
      )}
    </div>
  );
}
