"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useReducedMotion } from "framer-motion";

interface CharInfo {
  ch: string;
  italic: boolean;
}

// Mirrors the original static markup exactly (including the italic
// "measured" word) so the locked, single-render layout matches the
// previous static tagline pixel-for-pixel.
const SEGMENTS: { text: string; italic: boolean }[] = [
  { text: "Pakistan’s job market, ", italic: false },
  { text: "measured", italic: true },
  { text: ", not guessed.", italic: false },
];

const CHARS: CharInfo[] = SEGMENTS.flatMap((seg) =>
  seg.text.split("").map((ch) => ({ ch, italic: seg.italic }))
);
const TOTAL_CHARS = CHARS.length;
const FULL_TEXT = CHARS.map((c) => c.ch).join("");

// Index of the comma that closes "measured," -- the first character of the
// third segment, not the earlier comma after "market".
const MEASURED_COMMA_INDEX = SEGMENTS[0].text.length + SEGMENTS[1].text.length;

const BASE_DELAY_MS = 40;
const JITTER_MS = 18; // +/- range
const COMMA_PAUSE_MS = 420;
const LINE_BREAK_PAUSE_MS = 260;
const BLINK_DURATION_MS = 1000; // matches the caret-blink-twice keyframe
const FADE_DURATION_MS = 300;

const HEADLINE_CLASSES =
  "mt-6 max-w-3xl font-display text-4xl font-medium leading-[1.08] tracking-tight sm:text-5xl md:text-6xl text-balance";

type CaretPhase = "typing" | "blinking" | "fading" | "removed";

/** Types out the hero tagline once per page load. Every character is
 * rendered in the h1 from the first frame (visibility:hidden, not
 * display:none), so layout and line breaks are locked at mount and
 * revealing never reflows the page -- only visibility flips left to right. */
export function TypewriterTagline() {
  const reduce = useReducedMotion();
  const containerRef = useRef<HTMLHeadingElement>(null);
  const jitterRef = useRef<number[] | null>(null);
  const [revealCount, setRevealCount] = useState(reduce ? TOTAL_CHARS : 0);
  const [caretPhase, setCaretPhase] = useState<CaretPhase>("typing");

  if (jitterRef.current === null) {
    // Computed once, on first render, never recomputed on subsequent renders.
    jitterRef.current = CHARS.map(() => (Math.random() * 2 - 1) * JITTER_MS);
  }

  useEffect(() => {
    if (reduce || !containerRef.current) return;

    // Layout is already locked (every span is present, just hidden), so the
    // real wrap point can be measured immediately -- no need to wait.
    const spans = containerRef.current.querySelectorAll<HTMLSpanElement>("[data-char-index]");
    let lineBreakIndex = -1;
    if (spans.length > 0) {
      const firstTop = spans[0].getBoundingClientRect().top;
      for (let i = 1; i < spans.length; i++) {
        if (spans[i].getBoundingClientRect().top > firstTop + 1) {
          lineBreakIndex = i;
          break;
        }
      }
    }

    const jitter = jitterRef.current!;
    const timers: ReturnType<typeof setTimeout>[] = [];
    let cumulative = 0;

    for (let i = 0; i < TOTAL_CHARS; i++) {
      cumulative += BASE_DELAY_MS + jitter[i];
      if (i === MEASURED_COMMA_INDEX + 1) cumulative += COMMA_PAUSE_MS;
      if (i === lineBreakIndex) cumulative += LINE_BREAK_PAUSE_MS;

      const revealIndex = i + 1;
      timers.push(setTimeout(() => setRevealCount(revealIndex), cumulative));
    }

    timers.push(setTimeout(() => setCaretPhase("blinking"), cumulative));
    timers.push(setTimeout(() => setCaretPhase("fading"), cumulative + BLINK_DURATION_MS));
    timers.push(
      setTimeout(() => setCaretPhase("removed"), cumulative + BLINK_DURATION_MS + FADE_DURATION_MS)
    );

    return () => timers.forEach(clearTimeout);
  }, [reduce]);

  const caretClass = [
    "inline-block w-px h-[0.85em] align-middle -mb-[0.05em] bg-java transition-opacity",
    caretPhase === "blinking" ? "animate-caret-blink-twice" : "",
    caretPhase === "fading" ? "opacity-0" : "opacity-100",
  ].join(" ");

  const showCaret = !reduce && caretPhase !== "removed";

  const nodes: ReactNode[] = [];
  CHARS.forEach((c, i) => {
    if (showCaret && i === revealCount) {
      nodes.push(
        <span
          key="caret"
          className={caretClass}
          style={{ transitionDuration: `${FADE_DURATION_MS}ms` }}
        />
      );
    }
    nodes.push(
      <span
        key={i}
        data-char-index={i}
        className={c.italic ? "italic" : undefined}
        style={{ visibility: reduce || i < revealCount ? "visible" : "hidden" }}
      >
        {c.ch}
      </span>
    );
  });
  if (showCaret && revealCount === TOTAL_CHARS) {
    nodes.push(
      <span
        key="caret"
        className={caretClass}
        style={{ transitionDuration: `${FADE_DURATION_MS}ms` }}
      />
    );
  }

  return (
    <div aria-label={FULL_TEXT}>
      <h1 ref={containerRef} className={HEADLINE_CLASSES} aria-hidden="true">
        {nodes}
      </h1>
    </div>
  );
}
