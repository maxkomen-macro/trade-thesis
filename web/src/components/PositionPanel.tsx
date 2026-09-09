import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../lib/api";
import type { PositionOut } from "../lib/types";
import { exitReasonLabel, fmtDate, fmtDateTime, fmtMoney, fmtPct, pnlColor } from "../lib/format";
import { Notice, Panel } from "./Panel";

/** One option position on an idea (Phase 6): legs as quoted at entry, the exit rules (editable while open), every
 *  stored daily mark, and the owner's actions. Every number is read from option_positions / option_snapshots. */
export function PositionPanel({ positionId, ideaId, canWrite }: { positionId: number; ideaId: number; canWrite: boolean }) {
  const qc = useQueryClient();
  const pos = useQuery({ queryKey: ["position", positionId], queryFn: () => api.get<PositionOut>(`/api/positions/${positionId}`) });
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [exitPrice, setExitPrice] = useState("");
  const [closeNote, setCloseNote] = useState("");
  const [rules, setRules] = useState<{ take_profit_pct: string; stop_loss_pct: string; time_stop_days_before_expiry: string } | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["position", positionId] });
    qc.invalidateQueries({ queryKey: ["positions", String(ideaId)] });
    qc.invalidateQueries({ queryKey: ["idea", String(ideaId)] });
    qc.invalidateQueries({ queryKey: ["ideas"] });
    qc.invalidateQueries({ queryKey: ["stats"] });
    qc.invalidateQueries({ queryKey: ["review"] });
  };
  const fail = (e: unknown) => {
    if (e instanceof ApiError) {
      const b = e.body as { detail?: unknown } | null;
      const d = b && typeof b === "object" ? b.detail : null;
      setErr(d && typeof d === "object" && "message" in (d as Record<string, unknown>) ? String((d as Record<string, unknown>).message) : e.message);
    } else setErr((e as Error).message);
  };
  const mark = useMutation({
    mutationFn: () => api.post<{ position: PositionOut; summary: { marked: unknown[]; closed: unknown[]; errors: Array<Record<string, unknown>>; chain_requests: number } }>(`/api/positions/${positionId}/mark`),
    onSuccess: (r) => {
      invalidate();
      const s = r.summary;
      setErr(s.errors.length ? `Mark failed: ${String(s.errors[0].error ?? JSON.stringify(s.errors[0]))}` : null);
      setInfo(s.errors.length ? null : `${s.marked.length ? "New mark stored" : "Chain unchanged since the last mark"}; ${s.chain_requests} chain requests; ${s.closed.length ? "position closed" : "still open"}.`);
    },
    onError: fail,
  });
  const save = useMutation({
    mutationFn: () =>
      api.patch<PositionOut>(`/api/positions/${positionId}`, {
        take_profit_pct: Number(rules!.take_profit_pct),
        stop_loss_pct: Number(rules!.stop_loss_pct),
        time_stop_days_before_expiry: Number(rules!.time_stop_days_before_expiry),
      }),
    onSuccess: () => {
      setRules(null);
      setErr(null);
      invalidate();
    },
    onError: fail,
  });
  const close = useMutation({
    mutationFn: () => api.post<PositionOut>(`/api/positions/${positionId}/close`, { exit_price: exitPrice.trim() ? Number(exitPrice) : null, note: closeNote || null }),
    onSuccess: () => {
      setErr(null);
      invalidate();
    },
    onError: fail,
  });
  const del = useMutation({
    mutationFn: () => api.del(`/api/positions/${positionId}`),
    onSuccess: invalidate,
    onError: fail,
  });

  if (pos.isLoading) return <Notice>Loading position…</Notice>;
  if (pos.error || !pos.data) return <Notice tone="wrong">Could not load the position: {(pos.error as Error)?.message}</Notice>;
  const p = pos.data;
  const hidden = p.dollars_hidden;
  const open = p.status === "open";
  const marks = [...p.snapshots].reverse();

  return (
    <Panel
      title={`Expression: ${p.name}`}
      right={
        <span className="text-xs" style={{ color: open ? "var(--color-open)" : "var(--color-muted)" }}>
          {open ? "open" : `closed · ${exitReasonLabel(p.exit_reason)}`}
        </span>
      }
    >
      <div className="grid gap-4 md:grid-cols-3">
        <div>
          <div className="text-xs text-muted">{p.kind} · expires {fmtDate(p.expiry)}</div>
          <table className="mt-2 w-full text-xs">
            <thead className="text-left text-muted">
              <tr>
                <th className="pb-1 font-normal">Leg</th>
                <th className="pb-1 text-right font-normal">Entry bid / ask</th>
                <th className="pb-1 text-right font-normal">IV</th>
              </tr>
            </thead>
            <tbody>
              {p.legs.map((lg) => (
                <tr key={lg.contract} className="border-t border-line">
                  <td className="num py-1">
                    {lg.side === "long" ? "+" : "−"}
                    {lg.qty} {lg.strike}
                    {lg.right[0]} <span className="text-muted">{lg.contract}</span>
                  </td>
                  <td className="num py-1 text-right">
                    {lg.entry_bid?.toFixed(2) ?? "–"} / {lg.entry_ask?.toFixed(2) ?? "–"}
                  </td>
                  <td className="num py-1 text-right">{lg.entry_iv != null ? `${(lg.entry_iv * 100).toFixed(0)}%` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <dl className="mt-3 space-y-1 text-sm">
            <Row k="Entry debit">
              <span className="num">
                {p.entry_debit.toFixed(2)} <span className="text-xs text-muted">{p.entry_source === "fill" ? "stated fill" : "chain mid"} · {fmtDateTime(p.entry_as_of)}</span>
              </span>
            </Row>
            <Row k="Contracts">
              <span className="num">{hidden ? "hidden" : `${p.contracts} · ${fmtMoney(p.entry_cost)} paid`}</span>
            </Row>
            <Row k="Underlying at entry">
              <span className="num">{p.entry_spot?.toFixed(2) ?? "–"}</span>
            </Row>
          </dl>
        </div>

        <div>
          <div className="text-xs text-muted">Exit rules, applied in this order after idea resolution</div>
          <dl className="mt-2 space-y-1.5 text-sm">
            {(
              [
                ["stop_loss_pct", "Stop loss, % of premium", `${p.stop_loss_pct}`, `at ${(p.entry_debit * (1 - p.stop_loss_pct / 100)).toFixed(2)}`],
                ["take_profit_pct", "Take profit, % of premium", `${p.take_profit_pct}`, `at ${(p.entry_debit * (1 + p.take_profit_pct / 100)).toFixed(2)}`],
                ["time_stop_days_before_expiry", "Time stop, days before expiry", `${p.time_stop_days_before_expiry}`, `on ${fmtDate(p.time_stop_date)}`],
              ] as Array<[keyof NonNullable<typeof rules>, string, string, string]>
            ).map(([key, label, value, sub]) => (
              <Row key={key} k={label}>
                {rules ? (
                  <input value={rules[key]} onChange={(e) => setRules({ ...rules, [key]: e.target.value })} className="num w-20 rounded border border-line bg-bg px-2 py-0.5 text-right text-sm outline-none focus:border-accent" />
                ) : (
                  <span className="num">
                    {value} <span className="text-xs text-muted">{sub}</span>
                  </span>
                )}
              </Row>
            ))}
            <Row k="Expiry">
              <span className="num">{fmtDate(p.expiry)}</span>
            </Row>
          </dl>
          {canWrite && open && (
            <div className="mt-3 flex gap-2 text-xs">
              {rules ? (
                <>
                  <button onClick={() => save.mutate()} disabled={save.isPending} className="rounded-md bg-accent px-3 py-1 font-medium text-bg disabled:opacity-50">
                    {save.isPending ? "Saving…" : "Save rules"}
                  </button>
                  <button onClick={() => setRules(null)} className="rounded-md border border-line px-3 py-1">
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  onClick={() => setRules({ take_profit_pct: String(p.take_profit_pct), stop_loss_pct: String(p.stop_loss_pct), time_stop_days_before_expiry: String(p.time_stop_days_before_expiry) })}
                  className="rounded-md border border-line px-3 py-1 hover:border-accent"
                >
                  Edit exit rules
                </button>
              )}
            </div>
          )}
          {!open && (
            <p className="mt-3 text-xs text-muted">
              Closed {fmtDate(p.exit_as_of)} at <span className="num">{p.exit_value?.toFixed(2)}</span> ({p.exit_source === "expiry_intrinsic" ? "intrinsic at the expiry close" : p.exit_source === "fill" ? "stated fill" : "chain mid"}):{" "}
              <span className="num" style={{ color: pnlColor(p.pnl_pct) }}>
                {fmtPct(p.pnl_pct, 1)}
              </span>{" "}
              on premium{!hidden && p.pnl_abs !== null ? `, ${fmtMoney(p.pnl_abs, 0, true)}` : ""}.
            </p>
          )}
          {p.note && <p className="mt-2 text-xs text-muted">Note: {p.note}</p>}
        </div>

        <div>
          <div className="text-xs text-muted">Actions</div>
          {!canWrite && <p className="mt-2 text-xs text-muted">Read only. Paste the write token in Settings to mark or close.</p>}
          {canWrite && (
            <div className="mt-2 space-y-2 text-sm">
              {open && (
                <button onClick={() => mark.mutate()} disabled={mark.isPending} className="w-full rounded-md border border-line px-3 py-1.5 hover:border-accent disabled:opacity-50">
                  {mark.isPending ? "Pulling the chain…" : "Mark from today's chain"}
                </button>
              )}
              {open && (
                <div className="space-y-1.5">
                  <input value={exitPrice} onChange={(e) => setExitPrice(e.target.value)} placeholder="Exit price (blank = last mark)" className="num w-full rounded-md border border-line bg-bg px-2 py-1 text-sm outline-none focus:border-accent" />
                  <input value={closeNote} onChange={(e) => setCloseNote(e.target.value)} placeholder="Close note (optional)" className="w-full rounded-md border border-line bg-bg px-2 py-1 text-sm outline-none focus:border-accent" />
                  <button onClick={() => close.mutate()} disabled={close.isPending} className="w-full rounded-md border border-line px-3 py-1.5 hover:border-accent disabled:opacity-50">
                    Close position
                  </button>
                </div>
              )}
              <button
                onClick={() => {
                  if (window.confirm("Delete this position and its marks?")) del.mutate();
                }}
                className="w-full rounded-md border border-wrong/40 px-3 py-1.5 text-wrong hover:border-wrong"
              >
                Delete position
              </button>
            </div>
          )}
          {info && <p className="mt-2 text-xs text-muted">{info}</p>}
          {err && (
            <div className="mt-2">
              <Notice tone="wrong">{err}</Notice>
            </div>
          )}
        </div>
      </div>

      <div className="mt-4">
        <div className="mb-1 text-xs text-muted">Daily marks · structure at the chain mid, long legs at mid and short legs at mid; settlement at intrinsic after expiry</div>
        {marks.length === 0 && <Notice>No marks stored yet.</Notice>}
        {marks.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-muted">
                <tr className="border-b border-line">
                  <th className="py-1 pr-3 font-normal">Date</th>
                  <th className="py-1 pr-3 text-right font-normal">Value</th>
                  <th className="py-1 pr-3 text-right font-normal">Bid / ask</th>
                  <th className="py-1 pr-3 text-right font-normal">On premium</th>
                  {!hidden && <th className="py-1 pr-3 text-right font-normal">P&amp;L</th>}
                  <th className="py-1 pr-3 text-right font-normal">Underlying</th>
                  <th className="py-1 pr-3 text-right font-normal">IV</th>
                  <th className="py-1 pr-3 text-right font-normal">Delta</th>
                  <th className="py-1 font-normal">Source</th>
                </tr>
              </thead>
              <tbody>
                {marks.map((m) => (
                  <tr key={m.as_of} className="border-b border-line/60">
                    <td className="num py-1 pr-3">{fmtDate(m.as_of)}</td>
                    <td className="num py-1 pr-3 text-right">{m.value.toFixed(2)}</td>
                    <td className="num py-1 pr-3 text-right text-muted">
                      {m.bid?.toFixed(2) ?? "–"} / {m.ask?.toFixed(2) ?? "–"}
                    </td>
                    <td className="num py-1 pr-3 text-right" style={{ color: pnlColor(m.pnl_pct) }}>
                      {fmtPct(m.pnl_pct, 1)}
                    </td>
                    {!hidden && (
                      <td className="num py-1 pr-3 text-right" style={{ color: pnlColor(m.pnl_abs) }}>
                        {fmtMoney(m.pnl_abs, 0, true)}
                      </td>
                    )}
                    <td className="num py-1 pr-3 text-right">{m.spot?.toFixed(2) ?? "–"}</td>
                    <td className="num py-1 pr-3 text-right">{m.iv != null ? `${(m.iv * 100).toFixed(0)}%` : "–"}</td>
                    <td className="num py-1 pr-3 text-right">{m.delta?.toFixed(2) ?? "–"}</td>
                    <td className="py-1 text-muted">{m.source === "expiry_intrinsic" ? "intrinsic at expiry close" : "chain mid"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Panel>
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
