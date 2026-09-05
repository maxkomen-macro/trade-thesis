import { Link } from "react-router-dom";
import type { IdeaOut } from "../lib/types";
import { DIRECTION_LABEL, STATUS_LABEL, fmtPct, pnlColor, statusColor } from "../lib/format";

export function StatusPill({ status }: { status: string }) {
  const c = statusColor(status);
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs"
      style={{ borderColor: c, color: c }}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: c }} />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function TypeTag({ type }: { type: "real" | "paper" }) {
  return type === "paper" ? (
    <span className="rounded border border-accent/50 px-1.5 py-0.5 text-[11px] text-accent">Paper</span>
  ) : (
    <span className="rounded border border-line px-1.5 py-0.5 text-[11px] text-muted">Real</span>
  );
}

export function DirectionTag({ direction }: { direction: string }) {
  const up = direction === "up" || direction === "outperform";
  const down = direction === "down" || direction === "underperform";
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted">
      <span className={up ? "text-right" : down ? "text-wrong" : "text-muted"}>{up ? "▲" : down ? "▼" : "◆"}</span>
      {DIRECTION_LABEL[direction] ?? direction}
    </span>
  );
}

export function ProgressBar({ idea }: { idea: IdeaOut }) {
  const pct = Math.max(0, Math.min(100, idea.progress_pct));
  const color =
    idea.status === "right" ? "var(--color-right)" : idea.status === "wrong" ? "var(--color-wrong)" : idea.status === "open" ? "var(--color-open)" : "var(--color-muted)";
  const label = idea.progress_kind === "price" ? "to target" : "of window";
  return (
    <div className="min-w-[120px]" title={`${pct.toFixed(0)}% ${label}`}>
      <div className="h-1.5 w-full overflow-hidden rounded-sm bg-line">
        <div className="h-full" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="num mt-1 text-[11px] text-muted">
        {pct.toFixed(0)}% {label}
      </div>
    </div>
  );
}

export function Pnl({ idea, showAbs = true }: { idea: IdeaOut; showAbs?: boolean }) {
  const pct = idea.hypothetical_pnl_pct;
  return (
    <span className="num" style={{ color: pnlColor(pct) }}>
      {fmtPct(pct, 1)}
      {showAbs && !idea.dollars_hidden && idea.hypothetical_pnl_abs !== null && (
        <span className="ml-1 text-xs text-muted">
          {idea.hypothetical_pnl_abs < 0 ? "−" : "+"}${Math.abs(idea.hypothetical_pnl_abs).toFixed(0)}
        </span>
      )}
    </span>
  );
}

export function IdeaLink({ idea }: { idea: IdeaOut }) {
  return (
    <Link to={`/ideas/${idea.id}`} className="hover:underline">
      <span className="ticker mr-2 text-accent">{idea.instrument.symbol.replace(/\.US$/, "")}</span>
      <span className="text-text">{idea.title}</span>
    </Link>
  );
}
