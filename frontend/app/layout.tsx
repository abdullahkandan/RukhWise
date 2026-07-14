import type { Metadata } from "next";
import { Fraunces, DM_Sans, JetBrains_Mono } from "next/font/google";
import { MotionConfig } from "framer-motion";
import { Header } from "@/components/Header";
import { PageTransition } from "@/components/PageTransition";
import "./globals.css";

// Display serif — sharper and more editorial than the now-common Playfair.
// Fraunces' negative optical-size axis gives it real ink-on-paper weight at
// display sizes, which is the whole "broadsheet" thesis of this identity.
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "900"],
  style: ["normal", "italic"],
});

// UI/data grotesque, named directly in the brief.
const dmSans = DM_Sans({
  variable: "--font-dm-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

// Numbers, timestamps, system text. Tabular figures by default.
const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Rukhwise — Pakistan's job market, measured",
  description:
    "Live measurement of Pakistan's job market: skills, salaries, and hiring signal, tracked daily.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${fraunces.variable} ${dmSans.variable} ${jetbrainsMono.variable}`}
      suppressHydrationWarning
    >
      <body className="min-h-dvh bg-cream text-java font-sans antialiased">
        <MotionConfig reducedMotion="user">
          <Header />
          <PageTransition>{children}</PageTransition>
        </MotionConfig>
      </body>
    </html>
  );
}
