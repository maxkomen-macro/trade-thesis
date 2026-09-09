import React, { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { IdeaOut, StatsOut } from "../lib/types";
import { exitReasonLabel, fmtDate, fmtMoney, fmtPct, pnlColor } from "../lib/format";
import { Notice, Panel, StatTile } from "../components/Panel";
import { DirectionTag, IdeaLink, Pnl, ProgressBar, StatusPill, TypeTag } from "../components/IdeaBits";

const STATUS_OPTIONS = ["all", "open", "right", "wrong", "expired", "closed_manual"] as const;

/** The hero sentence, from non-placeholder ideas only. */
function heroSentence(s?: StatsOut): React.ReactNode {
  if (!s) return "Loading the record…";
  if (s.ideas_logged === 0) return "No ideas logged yet. Write the idea before the trade.";
  if (s.resolved_count === 0)
    return (
      <>
        <em className="not-italic font-bold">{s.ideas_logged}</em> {s.ideas_logged === 1 ? "idea" : "ideas"} logged, none resolved yet.
      </>
    );
  return (
    <>
      {s.ideas_logged} {s.ideas_logged === 1 ? "idea" : "ideas"} logged. Right on direction{" "}
      <em className="not-italic font-bold">{fmtPct(s.direction_hit_rate, 0, false)}</em> of the time, full target hit{" "}
      <em className="not-italic font-bold">{fmtPct(s.target_hit_rate, 0, false)}</em>.
      {s.positions_closed > 0 && (
        <>
          {" "}
          The option beat the thesis in{" "}
          <em className="not-italic font-bold">
            {s.option_beat_thesis} of {s.positions_closed}
          </em>{" "}
          {s.positions_closed === 1 ? "trade" : "trades"}.
        </>
      )}
    </>
  );
}

function Record({ n, k, tone }: { n: string; k: string; tone?: "right" | "wrong" }) {
  const color = tone === "right" ? "text-right" : tone === "wrong" ? "text-wrong" : "text-text";
  return (
    <div>
      <div className={`font-heading text-3xl font-semibold tracking-tight ${color}`}>{n}</div>
      <div className="mt-1.5 text-xs text-muted">{k}</div>
    </div>
  );
}

export function Ledger() {
  const [status, setStatus] = useState<string>("all");
  const [type, setType] = useState<string>("all");
  const [tag, setTag] = useState("");
  const [q, setQ] = useState("");
  const [expression, setExpression] = useState<string>("all");

  const stats = useQuery({ queryKey: ["stats"], queryFn: () => api.get<StatsOut>("/api/stats") });
  const ideas = useQuery({ queryKey: ["ideas"], queryFn: () => api.get<IdeaOut[]>("/api/ideas") });

  const filtered = useMemo(() => {
    const rows = ideas.data ?? [];
    return rows.filter((i) => {
      if (status !== "all" && i.status !== status) return false;
      if (type !== "all" && i.idea_type !== type) return false;
      if (tag && !i.tags.includes(tag.toLowerCase())) return false;
      if (expression === "with" && !i.position) return false;
      if (expression === "open" && i.position?.status !== "open") return false;
      if (expression === "paper" && i.position) return false;
      if (q) {
        const hay = `${i.title} ${i.thesis_text} ${i.instrument.symbol} ${i.instrument.display_name}`.toLowerCase();
        if (!hay.includes(q.toLowerCase())) return false;
      }
      return true;
    });
  }, [ideas.data, status, type, tag, q, expression]);

  const allTags = useMemo(() => Array.from(new Set((ideas.data ?? []).flatMap((i) => i.tags))).sort(), [ideas.data]);
  const s = stats.data;

  return (
    <div className="space-y-5">
      {/* Lede: hero sentence + three record numbers, per design/trade-thesis-mockup.html. Placeholders excluded. */}
      <div className="grid gap-8 border-b border-line pb-8 md:grid-cols-[1.25fr_1fr] md:items-end">
        <div>
          <h1 className="max-w-[22ch] text-3xl font-medium leading-tight md:text-4xl">{heroSentence(s)}</h1>
          <p className="mt-4 max-w-[56ch] text-sm text-muted">
            Every idea is written before the trade, resolved by the tape, and scored on two things separately: was the call
            right, and did the expression capture it.
          </p>
          <div className="mt-5">
            <Link to="/new" className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg">
              New thesis
            </Link>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-4">
          <Record n={s ? fmtPct(s.target_hit_rate, 0, false) : "–"} k="hit full target inside window" />
          <Record
            n={s ? (s.dollars_hidden ? fmtPct(s.hypothetical_pnl_pct_avg, 1) : fmtMoney(s.hypothetical_pnl_abs, 0, true)) : "–"}
            k={s?.dollars_hidden ? "avg paper return per idea" : "paper P&L on assigned capital"}
            tone={s && (s.dollars_hidden ? s.hypothetical_pnl_pct_avg ?? 0 : s.hypothetical_pnl_abs ?? 0) >= 0 ? "right" : "wrong"}
          />
          <Record n={s ? String(s.open_count) : "–"} k={s && s.resolving_soon.length > 0 ? `open, ${s.resolving_soon.length} resolve within 14 days` : "open"} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatTile label="Ideas logged" value={s ? String(s.ideas_logged) : "–"} sub={s ? `${s.resolved_count} resolved` : undefined} />
        <StatTile label="Direction hit rate" value={s ? fmtPct(s.direction_hit_rate, 0, false) : "–"} sub="resolved ideas" />
        <StatTile label="Target hit rate" value={s ? fmtPct(s.target_hit_rate, 0, false) : "–"} sub="resolved ideas" />
        <StatTile
          label="Hypothetical P&L"
          value={s ? (s.dollars_hidden ? fmtPct(s.hypothetical_pnl_pct_avg, 1) : fmtMoney(s.hypothetical_pnl_abs, 0, true)) : "–"}
          sub={s?.dollars_hidden ? "avg per idea, paper" : "paper portfolio"}
          tone={s && (s.dollars_hidden ? s.hypothetical_pnl_pct_avg ?? 0 : s.hypothetical_pnl_abs ?? 0) >= 0 ? "right" : "wrong"}
        />
        <StatTile label="Open" value={s ? String(s.open_count) : "–"} tone="open" />
      </div>
      {s && s.seed_count > 0 && (
        <p className="text-xs text-muted">
          {s.seed_count} placeholder {s.seed_count === 1 ? "idea is" : "ideas are"} excluded from every figure above until its dates and rules are replaced.
        </p>
      )}

      <Panel
        title="Ideas"
        right={
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border border-line bg-bg px-2 py-1">
              {STATUS_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {o === "all" ? "All statuses" : o === "closed_manual" ? "Closed" : o[0].toUpperCase() + o.slice(1)}
                </option>
              ))}
            </select>
            <select value={type} onChange={(e) => setType(e.target.value)} className="rounded border border-line bg-bg px-2 py-1">
              <option value="all">Real + paper</option>
              <option value="real">Real</option>
              <option value="paper">Paper</option>
            </select>
            <select value={expression} onChange={(e) => setExpression(e.target.value)} className="rounded border border-line bg-bg px-2 py-1">
              <option value="all">Any expression</option>
              <option value="with">With option</option>
              <option value="open">Option open</option>
              <option value="paper">Thesis only</option>
            </select>
            <select value={tag} onChange={(e) => setTag(e.target.value)} className="rounded border border-line bg-bg px-2 py-1">
              <option value="">All tags</option>
              {allTags.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search"
              className="w-36 rounded border border-line bg-bg px-2 py-1 outline-none focus:border-accent"
            />
          </div>
        }
      >
        {ideas.isLoading && <Notice>Loading ideas…</Notice>}
        {ideas.error && <Notice tone="wrong">Could not load ideas: {(ideas.error as Error).message}</Notice>}
        {ideas.data && filtered.length === 0 && <Notice>No ideas match. Log one with New thesis.</Notice>}
        {filtered.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="py-2 pr-3 font-normal">Idea</th>
                  <th className="py-2 pr-3 font-normal">Direction</th>
                  <th className="py-2 pr-3 font-normal">Window</th>
                  <th className="py-2 pr-3 font-normal">Entry</th>
                  <th className="py-2 pr-3 font-normal">Progress</th>
                  <th className="py-2 pr-3 text-right font-normal">Underlying</th>
                  <th className="py-2 pr-3 text-right font-normal">Option</th>
                  <th className="py-2 font-normal">Status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((i) => (
                  <tr key={i.id} className="border-b border-line/60 align-top hover:bg-surface-2/60">
                    <td className="py-2.5 pr-3">
                      <IdeaLink idea={i} />
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        <TypeTag type={i.idea_type} />
                        {i.seed && (
                          <span className="rounded border border-accent/40 px-1.5 py-0.5 text-[11px] text-accent" title="Placeholder dates and rules; excluded from every statistic until replaced">
                            placeholder
                          </span>
                        )}
                        {i.tags.map((t) => (
                          <span key={t} className="text-[11px] text-muted">
                            #{t}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-2.5 pr-3">
                      <DirectionTag direction={i.direction} />
                      <div className="mt-1 text-[11px] text-muted">{i.success_rule_text}</div>
                    </td>
                    <td className="num py-2.5 pr-3 text-xs text-muted">
                      {fmtDate(i.window_start)} → {fmtDate(i.window_end)}
                      {i.days_left !== null && <div>{i.days_left}d left</div>}
                    </td>
                    <td className="num py-2.5 pr-3 text-xs">
                      {i.entry_price?.toFixed(2) ?? "–"}
                      {i.last_price !== null && <div className="text-muted">last {i.last_price.toFixed(2)}</div>}
                    </td>
                    <td className="py-2.5 pr-3">
                      <ProgressBar idea={i} />
                    </td>
                    <td className="py-2.5 pr-3 text-right">
                      <Pnl idea={i} />
                    </td>
                    <td className="py-2.5 pr-3 text-right">
                      {i.position ? (
                        <div className="num text-sm" style={{ color: pnlColor(i.position.pnl_pct) }} title={`${i.position.kind} · ${exitReasonLabel(i.position.exit_reason)}`}>
                          {fmtPct(i.position.pnl_pct, 0)}
                          <div className="text-[11px] text-muted">
                            {i.position.name.replace(/^\S+ /, "")}
                            {i.position.status === "closed" ? ` · ${exitReasonLabel(i.position.exit_reason)}` : ""}
                          </div>
                        </div>
                      ) : (
                        <span className="text-xs text-muted">–</span>
                      )}
                    </td>
                    <td className="py-2.5">
                      <StatusPill status={i.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Resolving soon">
          {!s && <Notice>Loading…</Notice>}
          {s && s.resolving_soon.length === 0 && <Notice>Nothing resolves in the next 14 days.</Notice>}
          {s && s.resolving_soon.length > 0 && (
            <ul className="space-y-2 text-sm">
              {s.resolving_soon.map((i) => (
                <li key={i.id} className="flex items-center justify-between gap-3">
                  <IdeaLink idea={i} />
                  <span className="num text-xs text-muted">
                    {fmtDate(i.window_end)} · {i.days_left}d
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Hit rate by regime">
          {!s && <Notice>Loading…</Notice>}
          {s && s.by_regime.length === 0 && <Notice>No ideas yet.</Notice>}
          {s && s.by_regime.length > 0 && (
            <ul className="space-y-2 text-sm">
              {s.by_regime.map((b) => (
                <li key={b.regime}>
                  <div className="flex items-center justify-between">
                    <span>{b.regime}</span>
                    <span className="num text-xs text-muted">
                      {b.resolved}/{b.ideas} resolved · direction {fmtPct(b.direction_hit_rate, 0, false)} · target{" "}
                      {fmtPct(b.target_hit_rate, 0, false)}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-sm bg-line">
                    <div className="h-full bg-right" style={{ width: `${b.direction_hit_rate ?? 0}%` }} />
                  </div>
                </li>
              ))}
              <li className="text-[11px] text-muted">Regime is the one Radar had pushed when the idea was logged; Unknown means none was stored.</li>
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
