import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, getWriteToken } from "../lib/api";
import { regimeColor } from "../lib/format";
import type { RegimeReadout } from "../lib/types";

const links = [
  { to: "/", label: "Ledger" },
  { to: "/new", label: "New thesis" },
  { to: "/review", label: "Review" },
  { to: "/settings", label: "Settings" },
];

export function TopBar() {
  const regime = useQuery({ queryKey: ["regime"], queryFn: () => api.get<RegimeReadout>("/api/regime"), refetchInterval: 5 * 60_000 });
  const hasToken = Boolean(getWriteToken());

  return (
    <header className="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-5 py-3">
        <NavLink to="/" className="flex items-baseline gap-2">
          <span className="font-heading text-lg font-semibold">Trade Thesis</span>
          <span className="text-xs text-muted">idea ledger</span>
        </NavLink>
        <nav className="flex items-center gap-1 text-sm">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.to === "/"}
              className={({ isActive }) =>
                `rounded-md px-2.5 py-1 ${isActive ? "bg-surface-2 text-text" : "text-muted hover:text-text"}`
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-4">
          <RegimePill data={regime.data} loading={regime.isLoading} />
          <span className="flex items-center gap-1.5 text-xs text-muted" title={hasToken ? "Write token stored in this browser" : "Read-only: paste the write token in Settings"}>
            <span className={`inline-block h-2 w-2 rounded-full ${hasToken ? "bg-accent" : "bg-line"}`} />
            {hasToken ? "Write mode" : "Read only"}
          </span>
        </div>
      </div>
    </header>
  );
}

function RegimePill({ data, loading }: { data?: RegimeReadout; loading: boolean }) {
  if (loading) return <span className="text-xs text-muted">Regime …</span>;
  if (!data?.available) {
    return (
      <span className="rounded-md border border-line px-2 py-1 text-xs text-muted" title="Radar has not pushed a regime yet">
        No regime pushed yet
      </span>
    );
  }
  const stale = (data.age_days ?? 0) > 4;
  const probs: Array<[string, number | null]> = [
    ["Goldilocks", data.prob_goldilocks],
    ["Overheating", data.prob_overheating],
    ["Stagflation", data.prob_stagflation],
    ["Recession Risk", data.prob_recession],
  ];
  return (
    <div
      className="flex items-center gap-3 rounded-md border border-line bg-surface-2 px-2.5 py-1"
      title={`Radar regime as of ${data.as_of} (pushed ${data.stored_at ?? "?"}, source ${data.source})`}
    >
      <span className="text-xs text-muted">Radar regime</span>
      <span className="text-sm font-medium" style={{ color: regimeColor(data.regime) }}>
        {data.regime}
      </span>
      <span className="num text-xs text-muted">
        {data.confidence !== null ? `${Math.round(data.confidence * 100)}%` : ""}
        {stale ? ` · ${data.age_days}d old` : ""}
      </span>
      <span className="flex h-2 w-24 overflow-hidden rounded-sm bg-line">
        {probs.map(([name, p]) => (
          <span
            key={name}
            style={{ width: `${Math.max(0, (p ?? 0) * 100)}%`, background: regimeColor(name) }}
            title={`${name} ${((p ?? 0) * 100).toFixed(0)}%`}
          />
        ))}
      </span>
    </div>
  );
}
