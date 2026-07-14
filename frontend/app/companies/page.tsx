import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { Footer } from "@/components/Footer";
import { Companies } from "@/components/sections/Companies";
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
        <Companies totalPostings={stats.total_postings} />
      </Section>
      <Footer />
    </main>
  );
}
