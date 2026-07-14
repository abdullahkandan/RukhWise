import { ReactNode } from "react";

interface SectionProps {
  children: ReactNode;
  register: "cream" | "java";
  className?: string;
  id?: string;
  /** Renders full-bleed, edge to edge behind the content column -- a direct
   * child of the outer <section> (which has no width constraint of its
   * own), not of the max-w-6xl content div. Content still stacks above it
   * via normal DOM order. */
  backdrop?: ReactNode;
}

/** Registers the section-level background swap that is this identity's
 * central device — cream-with-java-ink or full-bleed java-with-cream-ink,
 * never mixed within one surface. */
export function Section({ children, register, className = "", id, backdrop }: SectionProps) {
  const bg = register === "cream" ? "bg-cream text-java" : "bg-java text-cream";
  return (
    <section id={id} className={`scroll-mt-24 relative ${bg} ${className}`}>
      {backdrop && <div className="absolute inset-0 overflow-hidden">{backdrop}</div>}
      <div className="relative mx-auto max-w-6xl px-6 md:px-10">{children}</div>
    </section>
  );
}
