/** Formatting helpers. Every number on screen goes through one of these (mono font applied by the caller). */
export function fmtPct(v: number | null | undefined, digits = 1, signed = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const s = v.toFixed(digits);
  return (signed && v > 0 ? "+" : "") + s + "%";
}

export function fmtMoney(v: number | null | undefined, digits = 0, signed = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const abs = Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const sign = v < 0 ? "−" : signed && v > 0 ? "+" : "";
  return `${sign}$${abs}`;
}

export function fmtPrice(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Regime label → display color. Radar labels: Goldilocks, Overheating, Stagflation, Recession Risk. */
export function regimeColor(label: string | null | undefined): string {
  switch ((label ?? "").toLowerCase()) {
    case "goldilocks":
      return "var(--color-right)";
    case "overheating":
      return "var(--color-accent)";
    case "stagflation":
      return "#e67e22";
    case "recession risk":
    case "recession":
      return "var(--color-wrong)";
    default:
      return "var(--color-muted)";
  }
}

export const STATUS_LABEL: Record<string, string> = {
  open: "Open",
  right: "Right",
  wrong: "Wrong",
  expired: "Expired",
  closed_manual: "Closed",
};

export function statusColor(status: string): string {
  switch (status) {
    case "right":
      return "var(--color-right)";
    case "wrong":
      return "var(--color-wrong)";
    case "open":
      return "var(--color-open)";
    default:
      return "var(--color-muted)";
  }
}

export const DIRECTION_LABEL: Record<string, string> = {
  up: "Up",
  down: "Down",
  outperform: "Outperform",
  underperform: "Underperform",
  range: "Range",
};

export function pnlColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "var(--color-muted)";
  return v > 0 ? "var(--color-right)" : v < 0 ? "var(--color-wrong)" : "var(--color-muted)";
}
