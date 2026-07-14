"use client";

import { motion } from "framer-motion";
import { ReactNode } from "react";

interface ScrollRevealProps {
  children: ReactNode;
  className?: string;
  delay?: number;
  index?: number;
  staggerMs?: number;
}

/** Single scroll-reveal treatment used everywhere in the system: fade +
 * small rise, no bounce, no scale. `index` drives stagger for lists of
 * cards so items settle in sequence rather than all at once. */
export function ScrollReveal({
  children,
  className,
  delay = 0,
  index = 0,
  staggerMs = 60,
}: ScrollRevealProps) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={{
        duration: 0.5,
        delay: delay + (index * staggerMs) / 1000,
        ease: [0.16, 1, 0.3, 1],
      }}
    >
      {children}
    </motion.div>
  );
}
