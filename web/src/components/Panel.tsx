import type { ReactNode } from "react";

export function Panel({ title, children, className = "", right }: { title?: string; children: ReactNode; className?: string; right?: ReactNode }) {
  return (
    <section className={`rounded-lg border border-line bg-surface p-4 ${className}`}>
      {(title || right) && (
        <header className="mb-3 flex items-center justify-between">
          {title && <h2 className="text-base text-text">{title}</h2>}
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function StatTile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "right" | "wrong" | "open" | "accent" }) {
  const color =
    tone === "right" ? "text-right" : tone === "wrong" ? "text-wrong" : tone === "open" ? "text-open" : tone === "accent" ? "text-accent" : "text-text";
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={`num mt-1 text-xl font-medium ${color}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Notice({ tone = "muted", children }: { tone?: "muted" | "accent" | "wrong"; children: ReactNode }) {
  const cls = tone === "accent" ? "border-accent/40 text-accent" : tone === "wrong" ? "border-wrong/40 text-wrong" : "border-line text-muted";
  return <div className={`rounded-md border bg-surface-2 px-3 py-2 text-sm ${cls}`}>{children}</div>;
}
