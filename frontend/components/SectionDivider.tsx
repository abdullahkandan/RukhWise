import { SweepArrow } from "./SweepArrow";

interface SectionDividerProps {
  className?: string;
}

/** Static sweep flourish marking the register change between sections
 * (cream/java). Same geometry as the hero mark and chart baselines — one
 * motif doing three jobs, not three different decorations. */
export function SectionDivider({ className = "" }: SectionDividerProps) {
  return (
    <div className={`flex justify-center py-2 ${className}`} aria-hidden="true">
      <SweepArrow className="h-6 w-40 opacity-70" animate={false} />
    </div>
  );
}
