import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { DivergenceCell, ReviewBucket, ReviewOut } from "../lib/types";
import { Link } from "react-router-dom";
import { exitReasonLabel, fmtMoney, fmtPct, pnlColor } from "../lib/format";
import { Notice } from "../components/Panel";

/** Breakdowns per design/trade-thesis-mockup.html: the thesis-versus-expression matrix and the regime bars,
 *  plus outcome / tag / real-vs-paper / rule-type bars. Placeholder ideas are excluded server-side. */
export function Review() {
  const review = useQuery({ queryKey: ["review"], queryFn: () => api.get<ReviewOut>("/api/review") });
  const r = review.data;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl">Review</h1>
        <p className="text-sm text-muted">
          {r
            ? `${r.ideas} ${r.ideas === 1 ? "idea" : "ideas"} counted${r.seed_count ? `, ${r.seed_count} placeholder${r.seed_count === 1 ? "" : "s"} excluded` : ""}.`
            : "Loading…"}
        </p>
      </div>
      {review.error && <Notice tone="wrong">Could not load the review: {(review.error as Error).message}</Notice>}
      {r && r.ideas === 0 && <Notice>Nothing to review yet. Log an idea and let the resolver score it.</Notice>}

      {r && (
        <div className="grid gap-10 md:grid-cols-2">
          <section>
            <h2 className="text-lg">Thesis versus expression</h2>
            <div className="mt-4 grid grid-cols-[110px_1fr_1fr] gap-2">
              <div />
              <div className="self-end pb-1 text-xs text-muted">Option won</div>
              <div className="self-end pb-1 text-xs text-muted">Option lost</div>
              <div className="self-center text-xs text-muted">Thesis right</div>
              <Cell c={r.divergence.thesis_right_option_won} hot d="right call, right contract" />
              <Cell c={r.divergence.thesis_right_option_lost} d="right call, wrong contract" />
              <div className="self-center text-xs text-muted">Thesis wrong</div>
              <Cell c={r.divergence.thesis_wrong_option_won} d="lucky, not skilled" />
              <Cell c={r.divergence.thesis_wrong_option_lost} d="wrong call, wrong contract" />
            </div>
            <p className="mt-3 max-w-[52ch] text-xs text-muted">
              {r.divergence.note} Thesis right means the direction call was right; option won means the return on premium was positive. Both are read at the
              close that resolved each side.
            </p>
          </section>

          <section>
            <h2 className="text-lg">Direction hit rate by Radar regime</h2>
            <Bars buckets={r.by_regime} value={(b) => b.direction_hit_rate} label={(b) => fmtPct(b.direction_hit_rate, 0, false)} tone="hit" />
            <p className="mt-3 max-w-[52ch] text-xs text-muted">
              Regime is stamped at logging time from the latest Radar push, so the split reflects what the model said when the idea was
              written, not hindsight.
            </p>
          </section>

          <section>
            <h2 className="text-lg">By outcome</h2>
            <Bars buckets={r.by_outcome} value={(b) => (r.ideas ? (b.ideas / r.ideas) * 100 : 0)} label={(b) => `${b.ideas} · avg ${fmtPct(b.avg_pnl_pct, 1)}`} tone="outcome" />
          </section>

          <section>
            <h2 className="text-lg">By tag</h2>
            <Bars buckets={r.by_tag} value={(b) => b.direction_hit_rate} label={(b) => `${b.resolved}/${b.ideas} · ${fmtPct(b.direction_hit_rate, 0, false)}`} tone="hit" />
          </section>

          <section>
            <h2 className="text-lg">Real versus paper</h2>
            <Bars
              buckets={r.by_idea_type}
              value={(b) => b.target_hit_rate}
              label={(b) => `${b.ideas} · target ${fmtPct(b.target_hit_rate, 0, false)}${!r.dollars_hidden && b.total_pnl_abs !== null ? ` · ${fmtMoney(b.total_pnl_abs, 0, true)}` : ""}`}
              tone="hit"
            />
          </section>

          <section>
            <h2 className="text-lg">By rule type</h2>
            <Bars buckets={r.by_rule_type} value={(b) => b.target_hit_rate} label={(b) => `${b.resolved}/${b.ideas} · target ${fmtPct(b.target_hit_rate, 0, false)}`} tone="hit" />
            <p className="mt-3 text-xs text-muted">Level rules need the tape to reach a price; direction rules only need the sign at window end.</p>
          </section>
        </div>
      )}
    </div>
  );
}

function Cell({ c, hot, d }: { c: DivergenceCell; hot?: boolean; d: string }) {
  return (
    <div className={`rounded-lg border bg-surface p-4 ${hot && c.count > 0 ? "border-accent/60" : "border-line"}`}>
      <div className="font-heading text-2xl font-semibold">{c.count}</div>
      <div className="mt-1 text-xs text-muted">
        {c.avg_option_pnl_pct === null ? (
          "no positions here yet"
        ) : (
          <>
            {d}. Avg <span className="num" style={{ color: pnlColor(c.avg_option_pnl_pct) }}>{fmtPct(c.avg_option_pnl_pct, 0)}</span> on premium
            {c.avg_thesis_pnl_pct !== null ? `, thesis ${fmtPct(c.avg_thesis_pnl_pct, 1)}` : ""}
          </>
        )}
      </div>
      {c.positions.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11px] text-muted">
          {c.positions.slice(0, 4).map((p) => (
            <li key={`${p.idea_id}-${p.name}`} className="truncate">
              <Link to={`/ideas/${p.idea_id}`} className="hover:underline">
                <span className="ticker text-accent">{p.symbol.replace(/\.US$/, "")}</span> {p.name}
              </Link>{" "}
              <span className="num">{fmtPct(p.option_pnl_pct, 0)}</span> · {exitReasonLabel(p.exit_reason)}
            </li>
          ))}
          {c.positions.length > 4 && <li>and {c.positions.length - 4} more</li>}
        </ul>
      )}
    </div>
  );
}

function Bars({ buckets, value, label, tone }: { buckets: ReviewBucket[]; value: (b: ReviewBucket) => number | null; label: (b: ReviewBucket) => string; tone: "hit" | "outcome" }) {
  if (buckets.length === 0) return <p className="mt-3 text-sm text-muted">No data yet.</p>;
  return (
    <div className="mt-4 grid gap-3">
      {buckets.map((b) => {
        const v = value(b);
        const color =
          tone === "outcome"
            ? b.key === "right"
              ? "var(--color-right)"
              : b.key === "wrong"
                ? "var(--color-wrong)"
                : b.key === "open"
                  ? "var(--color-open)"
                  : "var(--color-muted)"
            : v === null
              ? "var(--color-line)"
              : v >= 60
                ? "var(--color-right)"
                : v < 45
                  ? "var(--color-wrong)"
                  : "var(--color-muted)";
        return (
          <div key={b.key} className="grid grid-cols-[110px_1fr_auto] items-center gap-3 text-sm">
            <span className="truncate text-text" title={b.key}>
              {b.key === "closed_manual" ? "closed" : b.key}
            </span>
            <div className="h-1.5 overflow-hidden rounded-sm bg-line">
              <div className="h-full" style={{ width: `${Math.max(0, Math.min(100, v ?? 0))}%`, background: color }} />
            </div>
            <span className="num text-right text-xs" style={{ color: tone === "outcome" ? pnlColor(b.avg_pnl_pct) : "var(--color-muted)" }}>
              {v === null && tone === "hit" ? "n/a" : label(b)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
