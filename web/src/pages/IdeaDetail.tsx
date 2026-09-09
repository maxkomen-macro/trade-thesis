import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, getWriteToken } from "../lib/api";
import type { IdeaDetail as IdeaDetailT, OptionAnalysis, SystemStatus } from "../lib/types";
import { exitReasonLabel, fmtDate, fmtDateTime, fmtMoney, fmtPct, pnlColor } from "../lib/format";
import { Notice, Panel } from "../components/Panel";
import { DirectionTag, StatusPill, TypeTag } from "../components/IdeaBits";
import { PriceChart } from "../components/PriceChart";
import { PositionPanel } from "../components/PositionPanel";

export function IdeaDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const canWrite = Boolean(getWriteToken());
  const idea = useQuery({ queryKey: ["idea", id], queryFn: () => api.get<IdeaDetailT>(`/api/ideas/${id}`) });
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<SystemStatus>("/api/status") });
  const analysis = useQuery({
    queryKey: ["options", id],
    queryFn: () => api.get<OptionAnalysis>(`/api/ideas/${id}/options`),
    retry: false,
    enabled: Boolean(status.data?.options_enabled),
  });
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["idea", id] });
    qc.invalidateQueries({ queryKey: ["ideas"] });
    qc.invalidateQueries({ queryKey: ["stats"] });
  };
  const resolve = useMutation({
    mutationFn: () => api.post<IdeaDetailT>(`/api/ideas/${id}/resolve`),
    onSuccess: invalidate,
    onError: (e) => setErr((e as Error).message),
  });
  const close = useMutation({
    mutationFn: () => api.post<IdeaDetailT>(`/api/ideas/${id}/close`, { note: note || null }),
    onSuccess: invalidate,
    onError: (e) => setErr((e as Error).message),
  });
  const del = useMutation({
    mutationFn: () => api.del(`/api/ideas/${id}`),
    onSuccess: () => {
      invalidate();
      nav("/");
    },
    onError: (e) => setErr((e as Error).message),
  });

  if (idea.isLoading) return <Notice>Loading idea…</Notice>;
  if (idea.error || !idea.data) return <Notice tone="wrong">Could not load idea: {(idea.error as Error)?.message}</Notice>;
  const i = idea.data;
  const seedNote = (i.parsed_json?.seed_note as string | undefined) ?? null;
  const refreshError = i.parsed_json?.price_refresh_error as Record<string, unknown> | undefined;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="mb-1 flex items-center gap-2">
            <span className="ticker text-lg text-accent">{i.instrument.symbol}</span>
            <span className="text-sm text-muted">{i.instrument.display_name}</span>
            <TypeTag type={i.idea_type} />
            {i.seed && <span className="rounded border border-accent/40 px-1.5 py-0.5 text-[11px] text-accent">placeholder</span>}
          </div>
          <h1 className="text-2xl">{i.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-muted">
            <DirectionTag direction={i.direction} />
            <span>
              {fmtDate(i.window_start)} → {fmtDate(i.window_end)}
            </span>
            {i.days_left !== null && <span className="num">{i.days_left}d left</span>}
            {i.tags.map((t) => (
              <span key={t} className="text-xs">
                #{t}
              </span>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <StatusPill status={i.status} />
          {status.data?.options_enabled && (
            <Link to={`/ideas/${i.id}/options`} className="rounded-md border border-line px-3 py-1.5 text-sm hover:border-accent">
              Options expression
            </Link>
          )}
        </div>
      </div>

      {seedNote && <Notice tone="accent">Seed placeholder: {seedNote}</Notice>}
      {refreshError && <Notice tone="wrong">Price history could not be fetched at creation: {String(refreshError.error)}. Use Resolve now.</Notice>}
      {err && <Notice tone="wrong">{err}</Notice>}
      {i.divergence && (
        <div
          className="rounded-md border bg-surface-2 px-3 py-2 text-sm"
          style={{
            borderColor: divergenceColor(i.divergence.cell),
            color: i.divergence.resolved ? divergenceColor(i.divergence.cell) : "var(--color-text)",
          }}
        >
          <span className="mr-2 text-xs text-muted">Thesis versus expression</span>
          {i.divergence.text}
          {i.divergence.option_beat_thesis !== null && (
            <span className="ml-2 text-xs text-muted">{i.divergence.option_beat_thesis ? "The option is ahead of the thesis." : "The thesis is ahead of the option."}</span>
          )}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Panel title="Thesis" className="md:col-span-2">
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{i.thesis_text}</p>
          {i.catalyst_note && (
            <p className="mt-3 text-xs text-muted">
              Catalyst: {i.catalyst_note}
              {i.catalyst_date ? ` (${fmtDate(i.catalyst_date)})` : ""}
            </p>
          )}
        </Panel>
        <Panel title="Outcome">
          <dl className="space-y-2 text-sm">
            <Row k={i.position ? "Thesis P&L, underlying" : "Hypothetical P&L"}>
              <span className="num" style={{ color: pnlColor(i.hypothetical_pnl_pct) }}>
                {fmtPct(i.hypothetical_pnl_pct, 2)}
                {!i.dollars_hidden && i.hypothetical_pnl_abs !== null && <span className="ml-2 text-muted">{fmtMoney(i.hypothetical_pnl_abs, 0, true)}</span>}
              </span>
            </Row>
            {i.position && (
              <Row k="Expression P&L, on premium">
                <span className="num" style={{ color: pnlColor(i.position.pnl_pct) }}>
                  {fmtPct(i.position.pnl_pct, 1)}
                  {!i.dollars_hidden && i.position.pnl_abs !== null && <span className="ml-2 text-muted">{fmtMoney(i.position.pnl_abs, 0, true)}</span>}
                </span>
                <div className="text-[11px] text-muted">
                  {i.position.name} · {i.position.status === "open" ? `marked ${fmtDate(i.position.last_value_as_of)}` : exitReasonLabel(i.position.exit_reason)}
                </div>
              </Row>
            )}
            <Row k="Capital assigned">
              <span className="num">{i.dollars_hidden ? "hidden" : fmtMoney(i.capital_assigned)}</span>
            </Row>
            <Row k="Entry">
              <span className="num">
                {i.entry_price?.toFixed(2) ?? "–"} <span className="text-xs text-muted">{fmtDateTime(i.entry_price_at)}</span>
              </span>
            </Row>
            <Row k="Last">
              {i.last_price === null ? (
                <span className="text-xs text-muted">{i.status === "open" ? "awaiting the first close inside the window" : "–"}</span>
              ) : (
                <span className="num">
                  {i.last_price.toFixed(2)} <span className="text-xs text-muted">{fmtDate(i.last_price_as_of)}</span>
                </span>
              )}
            </Row>
            <Row k="Success">
              <span>{i.success_rule_text}</span>
            </Row>
            <Row k="Invalidation">
              <span>
                {i.invalidation_rule_text ?? "none"}
                {i.invalidation_is_note_only ? " (note only)" : ""}
              </span>
            </Row>
            {i.benchmark_symbol && (
              <>
                <Row k={`Spread at resolution vs ${i.benchmark_symbol}`}>
                  <span className="num" style={{ color: pnlColor(i.spread_at_resolution_pct) }}>
                    {fmtPct(i.spread_at_resolution_pct, 2)}
                  </span>
                </Row>
                <Row k={`Spread at window end vs ${i.benchmark_symbol}`}>
                  <span className="num" style={{ color: pnlColor(i.spread_at_window_end_pct) }}>
                    {i.spread_at_window_end_pct === null ? (i.status === "open" ? "window open" : "pending") : fmtPct(i.spread_at_window_end_pct, 2)}
                  </span>
                </Row>
              </>
            )}
            <Row k="Regime at entry">
              <span>{i.radar_regime ?? "not stored"}</span>
            </Row>
            {status.data?.options_enabled && (
              <Row k="Expression">
                <Link to={`/ideas/${i.id}/options`} className="underline">
                  {analysis.data
                    ? analysis.data.verdict === "trade"
                      ? `Trade: ${analysis.data.candidates.find((c) => c.rank === 1)?.name ?? "see analysis"}`
                      : "No trade (see analysis)"
                    : analysis.error instanceof ApiError && analysis.error.status === 404
                      ? i.status === "open"
                        ? "not analysed yet"
                        : "none"
                      : analysis.isLoading
                        ? "…"
                        : "open analysis"}
                </Link>
                {analysis.data && (
                  <div className="text-[11px] text-muted">
                    analysed {fmtDateTime(analysis.data.created_at)}
                    {i.position ? "" : i.status === "open" ? " · no position taken" : ""}
                  </div>
                )}
              </Row>
            )}
            {i.status !== "open" && (
              <Row k="Resolution">
                <span>
                  {i.resolution_reason} · {fmtDateTime(i.resolved_at)}
                </span>
              </Row>
            )}
          </dl>
          <p className="mt-3 text-[11px] text-muted">Paper-portfolio figures. Every number traces to a stored EODHD snapshot.</p>
        </Panel>
      </div>

      <Panel title="Price" right={<span className="text-xs text-muted">close · entry dashed · target amber · stop red · window shaded</span>}>
        <PriceChart
          bars={i.bars}
          entry={i.entry_price}
          target={i.target_level}
          stop={i.stop_level}
          windowStart={i.window_start}
          windowEnd={i.window_end}
          events={i.events}
        />
      </Panel>

      {i.position && <PositionPanel positionId={i.position.id} ideaId={i.id} canWrite={canWrite} />}

      <div className="grid gap-4 md:grid-cols-3">
        <Panel title="Timeline" className="md:col-span-2">
          {i.events.length === 0 && <Notice>No events yet.</Notice>}
          <ul className="space-y-1.5 text-sm">
            {[...i.events].reverse().map((e) => (
              <li key={e.id} className="flex items-start gap-3">
                <span className="num w-24 shrink-0 text-xs text-muted">{fmtDate(e.occurred_on)}</span>
                <span className="w-24 shrink-0 text-xs" style={{ color: eventColor(e.event_type) }}>
                  {e.event_type.replace("_", " ")}
                </span>
                <span className="num text-xs text-muted">{e.price?.toFixed(2) ?? ""}</span>
                <span className="text-xs text-muted">{e.note}</span>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Actions">
          {!canWrite && <Notice>Read only. Paste the write token in Settings to act on this idea.</Notice>}
          {canWrite && (
            <div className="space-y-3 text-sm">
              <button onClick={() => resolve.mutate()} disabled={resolve.isPending} className="w-full rounded-md border border-line px-3 py-1.5 hover:border-accent">
                {resolve.isPending ? "Resolving…" : "Refresh prices and resolve now"}
              </button>
              {i.status === "open" && (
                <div className="space-y-2">
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="Close note (optional)"
                    className="w-full rounded-md border border-line bg-bg px-2 py-1 text-sm outline-none focus:border-accent"
                  />
                  <button onClick={() => close.mutate()} disabled={close.isPending} className="w-full rounded-md border border-line px-3 py-1.5 hover:border-accent">
                    Close manually at last close
                  </button>
                </div>
              )}
              <button
                onClick={() => {
                  if (window.confirm("Delete this idea and its events?")) del.mutate();
                }}
                className="w-full rounded-md border border-wrong/40 px-3 py-1.5 text-wrong hover:border-wrong"
              >
                Delete idea
              </button>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-muted">{k}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

function eventColor(t: string): string {
  if (t === "target_hit") return "var(--color-right)";
  if (t === "stop_hit") return "var(--color-wrong)";
  if (t === "progress") return "var(--color-open)";
  if (t.startsWith("position_")) return "var(--color-accent)";
  return "var(--color-muted)";
}

function divergenceColor(cell: string | null): string {
  if (cell === "thesis_right_option_won") return "var(--color-right)";
  if (cell === "thesis_right_option_lost" || cell === "thesis_wrong_option_won") return "var(--color-accent)";
  if (cell === "thesis_wrong_option_lost") return "var(--color-wrong)";
  return "var(--color-line)";
}
