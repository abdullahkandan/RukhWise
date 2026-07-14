import { SweepArrow } from "./SweepArrow";

interface WordmarkProps {
  className?: string;
  size?: "small" | "large";
  showSweep?: boolean;
  animateSweep?: boolean;
}

/**
 * The wordmark set in type (Fraunces), not the source PNG — re-inks freely
 * via currentColor/text-color utilities to match whichever section it sits
 * on, which the pasted raster logo could never do.
 */
export function Wordmark({
  className,
  size = "small",
  showSweep = false,
  animateSweep = false,
}: WordmarkProps) {
  const textSize = size === "large" ? "text-4xl md:text-5xl" : "text-xl md:text-2xl";

  return (
    <div className={className}>
      <span
        className={`font-display font-semibold tracking-tight ${textSize}`}
      >
        Rukh<span className="italic">Wise</span>
      </span>
      {showSweep && (
        <SweepArrow
          className="mt-1 h-4 w-28 md:h-5 md:w-36"
          animate={animateSweep}
          duration={1.2}
        />
      )}
    </div>
  );
}
