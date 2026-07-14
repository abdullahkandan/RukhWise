"use client";

import { useEffect, useRef, useState } from "react";
import { animate, useInView, useMotionValue, useReducedMotion } from "framer-motion";

interface CountUpProps {
  value: number;
  duration?: number;
  delay?: number;
  className?: string;
  formatter?: (value: number) => string;
  /** Plain string appended after the formatted number (e.g. "%") -- unlike
   * `formatter`, this is safe to pass from a Server Component, since a
   * function reference can't cross that boundary but a string can. */
  suffix?: string;
}

const defaultFormatter = (v: number) => Math.round(v).toLocaleString("en-US");

/** Counts up once when scrolled into view. Respects reduced-motion by
 * rendering the final value immediately instead of animating.
 *
 * The displayed text is driven by React state (not a MotionValue rendered
 * directly as children) so it never depends on a browser animation-frame
 * loop to reach the screen -- the settle-safety-net below is a plain
 * setState, guaranteed to paint even if the animation itself never runs. */
export function CountUp({
  value,
  duration = 1.4,
  delay = 0,
  className,
  formatter = defaultFormatter,
  suffix = "",
}: CountUpProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.6 });
  const prefersReducedMotion = useReducedMotion();
  const motionValue = useMotionValue(0);
  const settledRef = useRef(false);
  const [text, setText] = useState(() => `${formatter(0)}${suffix}`);

  useEffect(() => {
    return motionValue.on("change", (v) => setText(`${formatter(v)}${suffix}`));
  }, [motionValue, formatter, suffix]);

  useEffect(() => {
    if (!inView || settledRef.current) return;
    settledRef.current = true;
    if (prefersReducedMotion) {
      motionValue.set(value);
      return;
    }
    const controls = animate(motionValue, value, {
      duration,
      delay,
      ease: [0.16, 1, 0.3, 1],
    });
    return controls.stop;
  }, [inView, value, duration, delay, prefersReducedMotion, motionValue]);

  useEffect(() => {
    // Safety net: if the intersection observer backing `inView` never
    // calls back, or the animation-frame loop driving the count never
    // runs (both reproduced directly in this session), the value must
    // not stay stuck at its initial 0 forever.
    const timer = setTimeout(() => {
      if (!settledRef.current) {
        settledRef.current = true;
        motionValue.set(value);
      }
    }, 1500);
    return () => clearTimeout(timer);
  }, [value, motionValue]);

  return (
    <span ref={ref} className={className}>
      {text}
    </span>
  );
}
