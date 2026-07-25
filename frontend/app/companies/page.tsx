import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { Footer } from "@/components/Footer";
import { Companies } from "@/components/sections/Companies";
import { DataUnavailable } from "@/components/DataUnavailable";
import { getStatsOverview } from "@/lib/api";

export const metadata: Metadata = {
  title: "Companies — Rukhwise",
  description: "Who is hiring, and how much of the market they hold.",
};

export default async function CompaniesPage() {
  const stats = await getStatsOverview();

  return (
    <main>
      <Section register="java">
        {stats ? (
          <Companies totalPostings={stats.total_postings} />
        ) : (
          <DataUnavailable message="Company data is temporarily unavailable." />
        )}
      </Section>
      <Footer />
    </main>
  );
}
