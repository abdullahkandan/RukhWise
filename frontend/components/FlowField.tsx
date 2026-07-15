"use client";

import { useEffect, useRef } from "react";
import { useReducedMotion } from "framer-motion";
import gsap from "gsap";

type Density = "hero" | "quiet";

interface FlowFieldProps {
  density: Density;
  className?: string;
}

interface PathSpec {
  id: string;
  d: string;
  isRed: boolean;
  opacity: number;
}

/**
 * Hand-solved cubic beziers: every path enters near the top-left region and
 * exits near the bottom-right region, arcing either above ("u") or below
 * ("l") the central headline zone (x:300-1100, y:250-550) so it never
 * crosses through it. Control points were verified numerically (sampling
 * each curve at 2500 points, checking the zone is cleared with 20px+ of
 * margin) rather than eyeballed -- the zone is wide enough relative to the
 * canvas that a naive gentle bow undershoots it and clips the headline.
 */
const PATHS: PathSpec[] = [
  { id: "u0", d: "M -80 40 C 480 -80, 900 -80, 1520 620", isRed: false, opacity: 0.1 },
  { id: "u1", d: "M -60 130 C 460 -150, 920 -150, 1500 700", isRed: false, opacity: 0.12 },
  { id: "u2", d: "M -100 210 C 500 -175, 880 -175, 1560 760", isRed: false, opacity: 0.14 },
  { id: "u3", d: "M -40 300 C 520 -225, 940 -225, 1500 830", isRed: true, opacity: 0.07 },
  { id: "l0", d: "M -80 -60 C 480 1085, 900 1085, 1520 700", isRed: false, opacity: 0.11 },
  { id: "l1", d: "M -60 10 C 460 1035, 920 1035, 1500 780", isRed: false, opacity: 0.13 },
  { id: "l2", d: "M -100 70 C 500 975, 880 975, 1560 660", isRed: false, opacity: 0.12 },
  { id: "l3", d: "M -40 -30 C 520 1155, 940 1155, 1500 860", isRed: true, opacity: 0.07 },
];

// hero: all 8 base paths, comets duplicate 5 of them.
const HERO_COMET_IDS = ["u0", "u2", "u3", "l0", "l2"];
// quiet: a reduced 5-path subset, comets duplicate 2 of them.
const QUIET_PATH_IDS = ["u0", "u1", "u3", "l0", "l2"];
const QUIET_COMET_IDS = ["u0", "l0"];

const COMET_DURATIONS = [18, 21, 24, 27, 30];
const COMET_DELAYS = [0, 3, 6, 9, 12];

export function FlowField({ density, className }: FlowFieldProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const reduce = useReducedMotion();

  const scale = density === "hero" ? 1 : 0.6;
  const basePaths = density === "hero" ? PATHS : PATHS.filter((p) => QUIET_PATH_IDS.includes(p.id));
  const cometIds = density === "hero" ? HERO_COMET_IDS : QUIET_COMET_IDS;
  const cometPaths = basePaths.filter((p) => cometIds.includes(p.id));

  useEffect(() => {
    if (!svgRef.current) return;

    if (reduce) {
      console.log("[FlowField] reduced-motion branch: static base layer only, no comets.");
      console.log("[FlowField] 0 comet tweens created -- reduced-motion active, comets are skipped entirely.");
      const basePathEls = svgRef.current.querySelectorAll<SVGPathElement>("[data-layer='base']");
      basePathEls.forEach((path) => {
        gsap.set(path, { opacity: Number(path.dataset.opacity) });
      });
      return;
    }

    const ctx = gsap.context(() => {
      const basePathEls = svgRef.current!.querySelectorAll<SVGPathElement>("[data-layer='base']");
      basePathEls.forEach((path) => {
        gsap.set(path, { opacity: 0 });
        gsap.to(path, {
          opacity: Number(path.dataset.opacity),
          duration: 0.8,
          ease: "power1.out",
        });
      });

      const cometEls = svgRef.current!.querySelectorAll<SVGPathElement>("[data-layer='comet']");
      if (cometEls.length === 0) {
        console.log(
          "[FlowField] 0 comet tweens created -- no elements matched [data-layer='comet'] for this density."
        );
      }

      let cometTweensCreated = 0;
      cometEls.forEach((path, i) => {
        // Initial dashoffset is measured client-side, not baked into SSR
        // markup, since getTotalLength() is only available in the browser.
        const length = path.getTotalLength();
        const gap = Math.max(3000, length + 200);
        gsap.set(path, {
          attr: { "stroke-dasharray": `90 ${gap}`, "stroke-dashoffset": length },
          opacity: 0,
        });

        const duration = COMET_DURATIONS[i % COMET_DURATIONS.length];
        const delay = COMET_DELAYS[i % COMET_DELAYS.length];

        gsap.to(path, {
          opacity: Number(path.dataset.opacity),
          duration: 0.8,
          delay: 2 + delay,
          ease: "power1.out",
        });
        gsap.to(path, {
          attr: { "stroke-dashoffset": 0 },
          duration,
          delay: 2 + delay,
          ease: "none",
          repeat: -1,
        });
        cometTweensCreated++;
      });

      console.log(`[FlowField] ${cometTweensCreated} comet tweens created.`);
    }, svgRef);

    return () => ctx.revert();
  }, [reduce, density]);

  return (
    <svg
      ref={svgRef}
      viewBox="0 0 1440 900"
      preserveAspectRatio="xMidYMid slice"
      className={className ?? "absolute inset-0 h-full w-full"}
      aria-hidden="true"
      fill="none"
    >
      {basePaths.map((p) => (
        <path
          key={`base-${p.id}`}
          data-layer="base"
          data-opacity={p.opacity * scale}
          d={p.d}
          stroke={p.isRed ? "#4D0E12" : "#231815"}
          strokeWidth={1}
          strokeLinecap="round"
          style={{ opacity: reduce ? p.opacity * scale : 0 }}
        />
      ))}
      {!reduce &&
        cometPaths.map((p) => (
          <path
            key={`comet-${p.id}`}
            data-layer="comet"
            data-opacity={Math.min(0.26, p.opacity * scale * 2.2)}
            d={p.d}
            stroke={p.isRed ? "#4D0E12" : "#231815"}
            strokeWidth={1}
            strokeLinecap="round"
            style={{ opacity: 0 }}
          />
        ))}
    </svg>
  );
}
