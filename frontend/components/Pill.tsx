"use client";

interface PillProps {
  label: string;
  active: boolean;
  onClick: () => void;
  variant?: "cream" | "java";
}

/** Filter toggle. No default-shadow, no default-radius pill styling —
 * a full stadium border in ink, filled solid only when active. */
export function Pill({ label, active, onClick, variant = "cream" }: PillProps) {
  const activeStyle =
    variant === "cream"
      ? "bg-java text-cream border-java"
      : "bg-cream text-java border-cream";
  const inactiveStyle =
    variant === "cream"
      ? "bg-transparent text-java border-java/40 hover:border-java"
      : "bg-transparent text-cream border-cream/40 hover:border-cream";

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`min-h-[38px] rounded-full border px-4 py-1.5 font-sans text-sm font-medium transition-colors duration-150 cursor-pointer ${
        active ? activeStyle : inactiveStyle
      }`}
    >
      {label}
    </button>
  );
}
