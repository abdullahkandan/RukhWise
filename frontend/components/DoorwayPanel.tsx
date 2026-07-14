import Link from "next/link";
import { ScrollReveal } from "./ScrollReveal";
import { CountUp } from "./CountUp";

interface DoorwayPanelProps {
  index: number;
  eyebrow: string;
  title: string;
  description: string;
  teaserValue: number;
  teaserSuffix?: string;
  teaserLabel: string;
  href: string;
}

/**
 * A full editorial "table of contents" row, not a button or card. Each
 * doorway spans the full container width with a hairline divider above it
 * (the last one gets one below too), teaser stat right-aligned like a
 * pull-quote figure. The whole row is the link -- hover lifts it a
 * hair and draws a thin accent line beneath the title, both under 150ms.
 */
export function DoorwayPanel({
  index,
  eyebrow,
  title,
  description,
  teaserValue,
  teaserSuffix = "",
  teaserLabel,
  href,
}: DoorwayPanelProps) {
  return (
    <ScrollReveal index={index} staggerMs={80}>
      <Link
        href={href}
        className="group block border-t border-java/15 py-10 transition-transform duration-150 last:border-b hover:-translate-y-0.5 md:py-14"
      >
        <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between md:gap-10">
          <div className="max-w-xl">
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">{eyebrow}</p>
            <h3 className="mt-3 inline-block font-display text-3xl font-medium leading-tight transition-colors duration-150 group-hover:text-sceptre-bright md:text-4xl">
              {title}
            </h3>
            <span className="block h-px w-16 origin-left scale-x-0 bg-sceptre-bright transition-transform duration-150 group-hover:scale-x-100" />
            <p className="mt-3 font-sans text-sm leading-relaxed text-java/70">{description}</p>
          </div>
          <div className="shrink-0 md:text-right">
            <CountUp
              value={teaserValue}
              suffix={teaserSuffix}
              className="font-mono text-4xl font-semibold tabular-nums leading-none text-sceptre-bright md:text-5xl"
            />
            <p className="mt-2 font-mono text-xs text-java/50">{teaserLabel}</p>
          </div>
        </div>
      </Link>
    </ScrollReveal>
  );
}
