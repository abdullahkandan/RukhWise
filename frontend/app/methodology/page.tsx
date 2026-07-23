import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { Footer } from "@/components/Footer";

export const metadata: Metadata = {
  title: "Methodology — Rukhwise",
  description: "Sources, definitions, and known limitations behind Rukhwise's numbers.",
};

function Prose({ children }: { children: React.ReactNode }) {
  return <div className="mt-4 max-w-2xl font-sans text-[15px] leading-relaxed text-java/75">{children}</div>;
}

export default function MethodologyPage() {
  return (
    <main>
      <Section register="cream" className="pt-36 pb-24 md:pt-44 md:pb-32">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-java/50">Methodology</p>
        <h1 className="mt-3 max-w-2xl font-display text-4xl font-medium leading-tight md:text-5xl">
          How these numbers are made
        </h1>
        <p className="mt-6 max-w-2xl font-sans text-base leading-relaxed text-java/70">
          Every figure on this site traces back to a posting collected by an automated
          pipeline. This page states, plainly, where that data comes from, how it is
          cleaned, and where it still falls short.
        </p>

        <div className="mt-20 flex flex-col gap-16">
          <section>
            <h2 className="font-display text-2xl font-medium">Sources</h2>
            <Prose>
              <p>
                <strong className="text-java">Mustakbil.com</strong> is collected daily,
                unattended, via a scheduled GitHub Actions workflow. It has proven tolerant
                of plain HTTP requests, so collection runs without a headless browser.
              </p>
              <p className="mt-4">
                <strong className="text-java">Rozee.pk</strong> sits behind bot detection
                that plain requests cannot pass, so it is collected through a
                browser-automation session rather than on a fixed daily schedule. Coverage
                is periodic and session-based, not continuous — Rozee figures should be
                read as a sample of the market, not an exhaustive count.
              </p>
              <p className="mt-4">
                <strong className="text-java">Indeed</strong> is collected daily via
                JobSpy, the same automated cadence as Mustakbil. It surfaces zero salary
                data for this market — every Indeed-sourced posting has a null salary,
                not a missing one, so Indeed rows are excluded from salary aggregates
                entirely rather than silently counted as &ldquo;no data disclosed.&rdquo;
              </p>
              <p className="mt-4">
                <strong className="text-java">LinkedIn</strong> is collected locally,
                best-effort, not on the automated daily schedule. Its guest search
                endpoints (also via JobSpy) have been observed to return listings outside
                Pakistan despite a Pakistan-scoped location filter, so every LinkedIn row
                is checked against a known-city/country match before being stored —
                anything that doesn&rsquo;t resolve to Pakistan is dropped and logged, not
                kept. LinkedIn also resurfaces postings that are genuinely weeks old;
                those are still stored with their real posting date preserved, but
                LinkedIn is excluded from the forecasting engine&rsquo;s targets
                entirely, since a first-seen-today timestamp on a month-old listing would
                corrupt a weekly-actuals model that assumes recent collection means
                recent posting.
              </p>
              <p className="mt-4">
                <strong className="text-java">Corpus break, July 2026.</strong> Indeed
                and LinkedIn were added this month. A direct check against the existing
                corpus at the time showed near-zero overlap in employer names with
                Mustakbil and Rozee — these sources are broadening market coverage into
                employers the prior two weren&rsquo;t reaching, not simply adding volume
                to employers already tracked. Trend lines that span this addition should
                be read with that in mind: some of the jump is coverage, not organic
                market growth.
              </p>
            </Prose>
          </section>

          <section>
            <h2 className="font-display text-2xl font-medium">Deduplication</h2>
            <Prose>
              <p>
                A posting&rsquo;s identity is derived from a stable combination of its
                source, source-native ID or URL, and core fields (title, company). Where a
                source exposes a canonical ID, that ID is the key; where it does not, a
                fingerprint of the listing content stands in for one. Re-collecting the
                same listing on a later run updates it in place rather than creating a
                duplicate row.
              </p>
            </Prose>
          </section>

          <section>
            <h2 className="font-display text-2xl font-medium">Distinct-posting counting</h2>
            <Prose>
              <p>
                Employers frequently repost near-identical listings — same title, same
                company, same requirements, refreshed dates — to stay near the top of
                search results. Counting every such repost as a new signal would inflate
                demand for whatever skills happen to appear in template job ads. Where this
                pattern is detectable, repeated postings from the same employer with
                near-identical content are treated as one distinct posting rather than
                many, so skill and demand counts reflect hiring activity rather than
                reposting behavior.
              </p>
            </Prose>
          </section>

          <section>
            <h2 className="font-display text-2xl font-medium">Distinct-company counting</h2>
            <Prose>
              <p>
                Posting counts alone can be dominated by a single high-volume employer
                reposting variations of the same roles — one recruiter with 300 open
                requisitions looks, in raw counts, like ten independent signals of market
                demand for whatever skills happen to appear in their listings. Company
                count exists to correct for this: it measures how many distinct
                employers demand a skill, not how many postings mention it. A skill
                demanded by 20 different companies is a broader signal than one
                demanded 300 times by a single company, even if the raw posting count
                says otherwise. Both numbers are shown side by side rather than
                collapsed into one, because they answer different questions —
                posting count is about volume, company count is about breadth.
              </p>
            </Prose>
          </section>

          <section>
            <h2 className="font-display text-2xl font-medium">Bulk-poster exclusion</h2>
            <Prose>
              <p>
                Any single company holding more than 25% of all tracked postings is
                flagged as a bulk poster. The market page surfaces this as a
                concentration disclosure whenever it applies, with a toggle to
                recompute skill rankings, pairings, and companion lists with that
                company&rsquo;s postings excluded entirely. Nothing is deleted or
                hidden by default — exclusion is opt-in, because a bulk poster is
                real market activity, not noise to be silently discarded. The toggle
                exists so the underlying, less-concentrated signal is one click away
                whenever it&rsquo;s useful to see it.
              </p>
            </Prose>
          </section>

          <section>
            <h2 className="font-display text-2xl font-medium">Strong-match definition</h2>
            <Prose>
              <p>
                The skill gap analyzer answers a job seeker&rsquo;s question, not an
                economist&rsquo;s: not &ldquo;what share of the market matches your
                skills&rdquo; but &ldquo;how many jobs could you credibly apply to
                right now.&rdquo; A posting is a <strong className="text-java">strong
                match</strong> when the skills entered cover 70% or more of that
                posting&rsquo;s recognized technical demands — enough to be a
                credible candidate without requiring perfection. A{" "}
                <strong className="text-java">full match</strong> requires covering
                every technical skill the posting mentions, no exceptions; every full
                match is also a strong match. Postings with no recognized technical
                skill mentions at all are excluded from both counts — there is no
                match-strength question to answer about a listing the taxonomy
                cannot read. The &ldquo;learn this next&rdquo; ranking simulates
                learning exactly one additional skill at a time and counts how many
                currently-short-of-70%-postings would cross that line as a result,
                so it points at the single highest-leverage skill to learn next,
                not just the most commonly requested one.
              </p>
            </Prose>
          </section>

          <section>
            <h2 className="font-display text-2xl font-medium">Currency</h2>
            <Prose>
              <p>
                Currency is captured per posting at collection time rather than assumed.
                Mustakbil listings, where available, are read directly from posting detail
                pages. Rozee does not expose distinguishing currency information, so its
                salary figures are recorded as PKR by default, matching the overwhelming
                majority of the market it serves. A small number of postings that name a
                foreign currency outright (USD, AED, and similar) are recorded as such and
                excluded from PKR salary aggregates rather than silently converted or
                merged in — see the footnote on the salary figures for the current count.
              </p>
            </Prose>
          </section>

          <section>
            <h2 className="font-display text-2xl font-medium">Soft-skill flagging</h2>
            <Prose>
              <p>
                The skill taxonomy tags each entry as technical or soft. Soft skills —
                communication, teamwork, leadership, and the like — are matched the same
                way technical skills are, but are hidden from the default skill ranking.
                They appear in a large share of postings regardless of role or seniority,
                which makes them true but not informative as a ranking signal. They remain
                available behind an explicit toggle for anyone who wants them.
              </p>
            </Prose>
          </section>

          <section>
            <h2 className="font-display text-2xl font-medium">Known limitations</h2>
            <Prose>
              <ul className="flex flex-col gap-4">
                <li>
                  <strong className="text-java">Pharma / APIs collision.</strong> The
                  taxonomy&rsquo;s skill matcher works on substrings and short tokens. The
                  abbreviation &ldquo;API&rdquo; and the pharmaceutical-industry sense of
                  &ldquo;APIs&rdquo; (active pharmaceutical ingredients) can collide in
                  postings from that sector, which inflates the apparent count of a
                  technical skill in listings that have nothing to do with software.
                </li>
                <li>
                  <strong className="text-java">Rozee salary figures.</strong> Where Rozee
                  discloses a salary at all, it is typically a single figure rather than a
                  range, unlike Mustakbil&rsquo;s more frequent min–max bands. Rozee-sourced
                  salary points are folded into the same distribution regardless, which can
                  understate the true spread within a band.
                </li>
                <li>
                  <strong className="text-java">Sample recency.</strong> This pipeline has
                  been running for a limited window. Trend lines and forecasts are only as
                  reliable as the history behind them, and early readings should be treated
                  as provisional until more collection cycles accumulate.
                </li>
                <li>
                  <strong className="text-java">Taxonomy coverage by domain.</strong> Skill
                  coverage is currently deepest in technology and business-support roles and
                  materially thinner in trades, food service, healthcare and education. This
                  is measured, not estimated: a majority of postings in those domains
                  currently produce one or zero substantive skill matches. Taxonomy
                  expansion is underway and coverage will be re-measured against the same
                  metric.
                </li>
              </ul>
            </Prose>
          </section>
        </div>
      </Section>

      <Footer />
    </main>
  );
}
