"use client";

import { motion, useReducedMotion } from "framer-motion";

interface SweepArrowProps {
  className?: string;
  /** Draw the line on mount (hero use). Static otherwise (divider/flourish use). */
  animate?: boolean;
  duration?: number;
  delay?: number;
  /** Line weight in viewBox units. Bump this when the rendered box grows,
   * since stroke width doesn't scale with the SVG's CSS width/height. */
  strokeWidth?: number;
}

/**
 * The wordmark's rising-sweep-arrow, recreated as geometry rather than a
 * pasted image — this is the one recurring mark of the whole system (hero
 * underline, chart baselines, section dividers). Color always comes from
 * currentColor so it can be re-inked per section (java on cream, cream on
 * java) instead of shipping a fixed-color asset.
 */
export function SweepArrow({
  className,
  animate = false,
  duration = 1.2,
  delay = 0,
  strokeWidth = 5,
}: SweepArrowProps) {
  const prefersReducedMotion = useReducedMotion();
  const shouldAnimate = animate && !prefersReducedMotion;

  return (
    <svg
      viewBox="0 0 420 100"
      fill="none"
      className={className}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <motion.path
        d="M6 62 C 48 32, 92 84, 142 56 C 192 28, 232 80, 282 52 C 318 30, 340 16, 358 10"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={shouldAnimate ? { pathLength: 0 } : undefined}
        animate={shouldAnimate ? { pathLength: 1 } : undefined}
        transition={
          shouldAnimate
            ? { duration, delay, ease: [0.65, 0, 0.35, 1] }
            : undefined
        }
      />
      <motion.path
        d="M340 2 L390 8 L362 34 Z"
        fill="currentColor"
        initial={shouldAnimate ? { opacity: 0, scale: 0.5 } : undefined}
        animate={shouldAnimate ? { opacity: 1, scale: 1 } : undefined}
        style={shouldAnimate ? { transformOrigin: "364px 17px" } : undefined}
        transition={
          shouldAnimate
            ? { duration: 0.3, delay: delay + duration - 0.2, ease: "easeOut" }
            : undefined
        }
      />
    </svg>
  );
}
