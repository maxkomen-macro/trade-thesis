import type { OptionCandidate } from "../lib/types";

/** Payoff at expiry for up to three candidates (per share, P&L after the debit), with the target and spot marked.
 *  Points come from the API (`payoff_curve`); this component only draws them. Layout follows the mockup's SVG. */
export function PayoffChart({ candidates, target, spot }: { candidates: OptionCandidate[]; target: number | null; spot: number }) {
  const curves = candidates.filter((c) => c.payoff_curve && c.payoff_curve.length > 1);
  if (curves.length === 0) return null;
  const W = 800;
  const H = 220;
  const L = 50;
  const R = 30;
  const T = 16;
  const B = 40;
  const xs = curves.flatMap((c) => c.payoff_curve!.map((p) => p[0]));
  const ys = curves.flatMap((c) => c.payoff_curve!.map((p) => p[1]));
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(0, ...ys);
  const yMax = Math.max(0, ...ys);
  const yPad = (yMax - yMin) * 0.08 || 1;
  const x = (v: number) => L + ((v - xMin) / (xMax - xMin || 1)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - (yMin - yPad)) / (yMax + yPad - (yMin - yPad))) * (H - T - B);
  const colors = ["#f0b429", "#4a9eff", "#8b949e"];
  const ticks = 6;
  const xTicks = Array.from({ length: ticks }, (_, i) => xMin + ((xMax - xMin) * i) / (ticks - 1));

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Payoff at expiry for the candidate structures" className="block h-[220px] w-full">
        <line x1={L} y1={y(0)} x2={W - R} y2={y(0)} stroke="rgba(255,255,255,.18)" />
        <g fontFamily="IBM Plex Mono, ui-monospace, monospace" fontSize="11" fill="#8b949e">
          {xTicks.map((t) => (
            <text key={t} x={x(t)} y={H - 18} textAnchor="middle">
              {t.toFixed(t >= 100 ? 0 : 1)}
            </text>
          ))}
          <text x={L - 8} y={y(0) + 4} textAnchor="end">
            0
          </text>
          <text x={L - 8} y={y(yMax) + 4} textAnchor="end">
            {yMax >= 0 ? "+" : ""}
            {yMax.toFixed(1)}
          </text>
          {yMin < 0 && (
            <text x={L - 8} y={y(yMin) + 4} textAnchor="end">
              {yMin.toFixed(1)}
            </text>
          )}
        </g>
        {target !== null && target >= xMin && target <= xMax && (
          <line x1={x(target)} y1={T} x2={x(target)} y2={H - B + 4} stroke="#f0b429" strokeDasharray="4 5" />
        )}
        {spot >= xMin && spot <= xMax && <line x1={x(spot)} y1={T} x2={x(spot)} y2={H - B + 4} stroke="#8b949e" strokeDasharray="2 5" />}
        {curves.map((c, i) => (
          <polyline
            key={c.name}
            points={c.payoff_curve!.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ")}
            fill="none"
            stroke={colors[i] ?? "#8b949e"}
            strokeWidth={i === 0 ? 2.4 : 1.8}
            strokeDasharray={i === 2 ? "5 4" : undefined}
          />
        ))}
      </svg>
      <div className="mt-2 flex flex-wrap gap-5 text-xs text-muted">
        {curves.map((c, i) => (
          <span key={c.name} className="flex items-center gap-2">
            <i className="inline-block h-0.5 w-4" style={{ background: colors[i] ?? "#8b949e" }} />
            {c.name}
          </span>
        ))}
        <span className="flex items-center gap-2">
          <i className="inline-block h-0.5 w-4 border-t border-dashed border-accent" /> target
        </span>
        <span className="flex items-center gap-2">
          <i className="inline-block h-0.5 w-4 border-t border-dotted border-muted" /> spot
        </span>
      </div>
    </div>
  );
}
