"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export interface ComboboxOption {
  value: string;
  label: string;
}

interface ComboboxProps {
  options: ComboboxOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  ariaLabel: string;
  /** Small color dot rendered before the input, e.g. "bg-sceptre" — used to
   * carry the compare module's series-color coding onto the trigger. */
  dotClassName?: string;
  /** cream = sits on a cream-register section, java = sits on a dark one. */
  variant?: "cream" | "java";
  className?: string;
}

/**
 * A from-scratch, palette-native searchable select — type to filter,
 * arrow/enter to navigate, click-outside or escape to close. Built in-house
 * rather than pulling a component library so nothing here carries default
 * shadcn/Radix chrome; every surface, border, and hover state is one of our
 * own tokens.
 */
export function Combobox({
  options,
  value,
  onChange,
  placeholder = "Search…",
  ariaLabel,
  dotClassName,
  variant = "cream",
  className = "",
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedLabel = options.find((o) => o.value === value)?.label ?? "";

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, query]);

  useEffect(() => {
    function onPointerDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  useEffect(() => {
    setHighlighted(0);
  }, [query, open]);

  function commit(option: ComboboxOption) {
    onChange(option.value);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      setOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const opt = filtered[highlighted];
      if (opt) commit(opt);
    } else if (e.key === "Escape") {
      setOpen(false);
      setQuery("");
      inputRef.current?.blur();
    }
  }

  const isCream = variant === "cream";
  const triggerText = isCream ? "text-java" : "text-cream";
  const triggerBorder = open
    ? isCream
      ? "border-java"
      : "border-cream"
    : isCream
      ? "border-java/30"
      : "border-cream/30";
  const panelBg = isCream ? "bg-cream" : "bg-soil";
  const panelBorder = isCream ? "border-java/20" : "border-cream/20";
  const optionHighlighted = isCream ? "bg-java text-cream" : "bg-cream text-java";
  const optionMuted = isCream ? "text-java/40" : "text-cream/40";

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <div
        className={`flex min-h-[44px] items-center gap-2 border bg-transparent px-3 py-2 transition-colors duration-150 ${triggerBorder}`}
      >
        {dotClassName && <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dotClassName}`} aria-hidden />}
        <input
          ref={inputRef}
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          aria-autocomplete="list"
          className={`w-full min-w-0 bg-transparent font-mono text-sm outline-none ${triggerText} placeholder:${isCream ? "text-java/40" : "text-cream/40"}`}
          value={open ? query : selectedLabel}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onKeyDown={onKeyDown}
        />
        <span className={`shrink-0 font-mono text-xs ${optionMuted}`} aria-hidden>
          {open ? "↵" : "▾"}
        </span>
      </div>

      {open && (
        <ul
          role="listbox"
          className={`absolute z-20 mt-1 max-h-64 w-full overflow-y-auto border ${panelBorder} ${panelBg} shadow-none`}
        >
          {filtered.length === 0 && (
            <li className={`px-3 py-2 font-sans text-sm ${optionMuted}`}>No matching skill</li>
          )}
          {filtered.map((option, i) => (
            <li
              key={option.value}
              role="option"
              aria-selected={option.value === value}
              onMouseDown={(e) => {
                e.preventDefault();
                commit(option);
              }}
              onMouseEnter={() => setHighlighted(i)}
              className={`cursor-pointer px-3 py-2 font-sans text-sm transition-colors duration-100 ${
                i === highlighted ? optionHighlighted : isCream ? "text-java" : "text-cream"
              }`}
            >
              {option.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
