import type { SkillTopEntry } from "@/lib/api";
import { ScrollReveal } from "../ScrollReveal";
import { SkillGapPicker } from "../SkillGapPicker";

interface SkillGapAnalyzerSectionProps {
  skills: SkillTopEntry[];
}

export function SkillGapAnalyzerSection({ skills }: SkillGapAnalyzerSectionProps) {
  return (
    <div className="py-24 md:py-32">
      <ScrollReveal>
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">
          The skill gap analyzer
        </p>
        <h2 className="mt-3 max-w-2xl font-display text-3xl font-medium leading-tight md:text-4xl">
          What should you learn next?
        </h2>
        <p className="mt-3 max-w-lg font-sans text-sm text-java/70">
          Tell it what you already know. It tells you, honestly, how far that gets you — and
          what to learn next for the biggest return.
        </p>
      </ScrollReveal>

      <div className="mt-14">
        <SkillGapPicker skills={skills} />
      </div>
    </div>
  );
}
