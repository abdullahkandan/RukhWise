"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { Wordmark } from "./Wordmark";

const NAV_ITEMS = [
  { label: "Analyzer", href: "/analyzer" },
  { label: "Market", href: "/market" },
  { label: "Companies", href: "/companies" },
  { label: "Engine", href: "/engine" },
  { label: "Methodology", href: "/methodology" },
];

const NAV_LINK_CLASS =
  "font-mono text-xs uppercase tracking-[0.16em] transition-opacity duration-150 hover:opacity-60";

/**
 * Sticky masthead, persists across every route. On Home it's transparent
 * over the hero (full-ink java on cream) until scrolled past, then commits
 * to a solid java bar -- every other route has no hero to be transparent
 * over, so it's solid from the first frame there. Active route gets full
 * opacity + an underline in the same small-caps mono style; everything else
 * dims on hover only.
 */
export function Header() {
  const pathname = usePathname();
  const isHome = pathname === "/";
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    if (!isHome) {
      setScrolled(true);
      return;
    }
    const onScroll = () => setScrolled(window.scrollY > window.innerHeight * 0.7);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [isHome]);

  // Solid whenever we've scrolled past the hero (or there's no hero at all)
  // OR the mobile menu is open, so the expanding list never floats over content.
  const solid = scrolled || open;

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 border-b transition-colors duration-300 ${
        solid
          ? "border-cream/10 bg-java/95 text-cream backdrop-blur-sm"
          : "border-transparent bg-transparent text-java"
      }`}
    >
      <div className="mx-auto max-w-6xl px-6 md:px-10">
        <div className="flex items-center justify-between gap-4 py-5">
          <Link href="/" className="shrink-0" onClick={() => setOpen(false)}>
            <Wordmark size="small" />
          </Link>

          <nav className="hidden items-center gap-6 lg:flex">
            {NAV_ITEMS.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`${NAV_LINK_CLASS} ${
                    active ? "underline underline-offset-4 opacity-100" : "opacity-80"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls="mobile-nav"
            className={`${NAV_LINK_CLASS} shrink-0 lg:hidden`}
          >
            {open ? "Close" : "Menu"}
          </button>
        </div>

        <AnimatePresence initial={false}>
          {open && (
            <motion.nav
              id="mobile-nav"
              key="mobile-nav"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
              className="overflow-hidden lg:hidden"
            >
              <div className="flex flex-col items-start gap-5 pb-6 pt-1">
                {NAV_ITEMS.map((item) => {
                  const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={`${NAV_LINK_CLASS} ${active ? "underline underline-offset-4" : ""}`}
                      onClick={() => setOpen(false)}
                    >
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            </motion.nav>
          )}
        </AnimatePresence>
      </div>
    </header>
  );
}
