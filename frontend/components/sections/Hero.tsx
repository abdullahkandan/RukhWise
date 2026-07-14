import { CountUp } from "../CountUp";

interface HeroProps {
  totalPostings: number;
  skillsMonitored: number;
  hoursSinceCollection: number;
}

const STAT_LABEL_CLASS =
  "font-sans text-xs md:text-sm uppercase tracking-[0.14em] text-java/60";
const STAT_NUMBER_CLASS =
  "font-mono tabular-nums font-semibold text-6xl sm:text-7xl md:text-8xl leading-none";

export function Hero({ totalPostings, skillsMonitored, hoursSinceCollection }: HeroProps) {
  return (
    <div className="flex min-h-[92dvh] flex-col justify-center py-20 md:py-28">
      <div className="relative z-10">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/60">
          Rukhwise
        </p>

        <h1 className="mt-6 max-w-3xl font-display text-4xl font-medium leading-[1.08] tracking-tight sm:text-5xl md:text-6xl text-balance">
          Pakistan&rsquo;s job market,{" "}
          <span className="italic">measured</span>, not guessed.
        </h1>

        <div className="mt-16 grid grid-cols-1 gap-10 sm:grid-cols-3 sm:gap-6 md:mt-20">
          <div>
            <CountUp value={totalPostings} className={STAT_NUMBER_CLASS} delay={0.2} />
            <p className={`mt-2 ${STAT_LABEL_CLASS}`}>Postings tracked</p>
          </div>
          <div>
            <CountUp value={skillsMonitored} className={STAT_NUMBER_CLASS} delay={0.3} />
            <p className={`mt-2 ${STAT_LABEL_CLASS}`}>Skills monitored</p>
          </div>
          <div>
            <CountUp value={hoursSinceCollection} className={STAT_NUMBER_CLASS} delay={0.4} />
            <p className={`mt-2 ${STAT_LABEL_CLASS}`}>Hours since last collection</p>
          </div>
        </div>
      </div>
    </div>
  );
}
