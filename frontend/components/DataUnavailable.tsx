interface DataUnavailableProps {
  message?: string;
}

/**
 * Honest empty state for a build-time data fetch that failed -- see
 * lib/api.ts's apiFetch, which returns null instead of throwing so a
 * stale or down API never fails the whole build. Never fabricates zeros;
 * just says the data isn't there right now. Uses opacity rather than an
 * explicit ink color, so it reads correctly inside either Section
 * register (cream or java) without needing a `dark` prop.
 */
export function DataUnavailable({
  message = "This data is temporarily unavailable. Check back shortly.",
}: DataUnavailableProps) {
  return (
    <div className="py-16 text-center">
      <p className="font-mono text-xs uppercase tracking-[0.16em] opacity-40">Temporarily unavailable</p>
      <p className="mt-3 font-sans text-sm opacity-60">{message}</p>
    </div>
  );
}
