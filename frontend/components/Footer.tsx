import Link from "next/link";
import { Wordmark } from "./Wordmark";

export function Footer() {
  return (
    <footer className="bg-java text-cream">
      <div className="mx-auto flex max-w-6xl flex-col gap-10 px-6 py-16 md:flex-row md:items-end md:justify-between md:px-10 md:py-20">
        <div>
          <Wordmark size="large" />
          <p className="mt-4 max-w-sm font-sans text-sm leading-relaxed text-cream/60">
            A daily measurement of Pakistan&rsquo;s job market — postings, skills,
            and salaries, collected and graded in the open.
          </p>
        </div>

        <div className="flex flex-col items-start gap-3 font-mono text-xs uppercase tracking-[0.16em] md:items-end">
          <Link
            href="https://github.com/abdullahkandan/RukhWise"
            target="_blank"
            rel="noopener noreferrer"
            className="text-cream/70 underline-offset-4 hover:text-cream hover:underline"
          >
            GitHub
          </Link>
          <Link
            href="/methodology"
            className="text-cream/70 underline-offset-4 hover:text-cream hover:underline"
          >
            Methodology
          </Link>
          <p className="text-cream/40">
            Built by{" "}
            <Link
              href="https://github.com/abdullahkandan"
              target="_blank"
              rel="noopener noreferrer"
              className="underline-offset-4 hover:text-cream/70 hover:underline"
            >
              Abdullah Kandan
            </Link>
          </p>
        </div>
      </div>
    </footer>
  );
}
