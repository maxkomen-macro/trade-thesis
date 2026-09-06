import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getWriteToken, setWriteToken } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import type { AppSettings, JobSummary, ServiceStatus, SystemStatus } from "../lib/types";
import { Notice, Panel } from "../components/Panel";

export function SettingsPage() {
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<SystemStatus>("/api/status") });
  const [token, setToken] = useState(getWriteToken());
  const [saved, setSaved] = useState(false);
  const refresh = useMutation({ mutationFn: () => api.post<JobSummary>("/api/jobs/refresh-prices") });
  const qc = useQueryClient();
  const canWrite = Boolean(getWriteToken());
  const [edits, setEdits] = useState<Partial<Record<keyof AppSettings, string | boolean>>>({});
  const saveSettings = useMutation({
    mutationFn: (body: Partial<AppSettings>) => api.patch<AppSettings>("/api/settings", body),
    onSuccess: () => {
      setEdits({});
      qc.invalidateQueries({ queryKey: ["status"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["ideas"] });
    },
  });
  function submitSettings() {
    const body: Partial<AppSettings> = {};
    for (const [k, v] of Object.entries(edits)) {
      if (typeof v === "boolean") (body as Record<string, unknown>)[k] = v;
      else if (v !== "" && !Number.isNaN(Number(v))) (body as Record<string, unknown>)[k] = Number(v);
    }
    if (Object.keys(body).length) saveSettings.mutate(body);
  }

  function save() {
    setWriteToken(token.trim());
    setSaved(true);
    setTimeout(() => window.location.reload(), 300);
  }

  return (
    <div className="space-y-5">
      <h1 className="text-2xl">Settings</h1>
      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Write token">
          <p className="mb-2 text-sm text-muted">Stored only in this browser. Sent as a bearer token on every write.</p>
          <div className="flex gap-2">
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste TT_WRITE_TOKEN"
              className="num flex-1 rounded-md border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent"
            />
            <button onClick={save} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg">
              Save
            </button>
          </div>
          {saved && <p className="mt-2 text-xs text-accent">Saved. Reloading…</p>}
        </Panel>
        <Panel title="Connections" right={<button onClick={() => status.refetch()} className="text-xs text-muted underline">Re-check</button>}>
          {status.isLoading && <Notice>Checking…</Notice>}
          {status.error && <Notice tone="wrong">Status call failed: {String((status.error as Error).message)}</Notice>}
          {status.data && (
            <ul className="space-y-2 text-sm">
              <ServiceRow s={status.data.db} />
              <ServiceRow s={status.data.eodhd} />
              <ServiceRow s={status.data.radar} />
            </ul>
          )}
        </Panel>
      </div>
      <Panel title="Prices">
        <p className="mb-2 text-sm text-muted">The daily cron resolves ideas at 21:30 UTC. Pull the latest closes now for every open idea.</p>
        <button
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending || !getWriteToken()}
          className="rounded-md border border-line px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50"
        >
          {refresh.isPending ? "Refreshing…" : "Refresh prices now"}
        </button>
        {refresh.data && (
          <p className="num mt-2 text-xs text-muted">
            {refresh.data.instruments_refreshed} instruments, {refresh.data.bars_added} new bars, {refresh.data.ideas_checked} ideas checked,{" "}
            {refresh.data.resolved.length} resolved, {refresh.data.errors.length} errors
          </p>
        )}
        {refresh.error && <Notice tone="wrong">{(refresh.error as Error).message}</Notice>}
      </Panel>
      <Panel
        title="Defaults"
        right={
          canWrite ? (
            <button onClick={submitSettings} disabled={saveSettings.isPending || Object.keys(edits).length === 0} className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-bg disabled:opacity-50">
              {saveSettings.isPending ? "Saving…" : "Save changes"}
            </button>
          ) : undefined
        }
      >
        {status.data ? (
          <>
            <table className="w-full text-sm">
              <tbody>
                {(
                  [
                    ["default_capital", "Default capital per idea", "number"],
                    ["account_size", "Account size, $ (hidden from public viewers)", "number"],
                    ["default_risk_pct", "Risk budget, % of account size", "number"],
                    ["public_hide_dollars", "Hide dollars from public viewers", "bool"],
                    ["options_enabled", "Options selector enabled", "bool"],
                    ["default_take_profit_pct", "Default take profit %", "number"],
                    ["default_stop_loss_pct", "Default stop loss %", "number"],
                    ["default_time_stop_days_before_expiry", "Default time stop, days before expiry", "number"],
                    ["risk_free_rate_pct", "Risk-free rate for option pricing, % (model assumption)", "number"],
                  ] as Array<[keyof AppSettings, string, "number" | "bool"]>
                ).map(([key, label, kind]) => {
                  const current = status.data!.settings[key] as number | boolean | null;
                  const edited = edits[key];
                  return (
                    <tr key={key} className="border-t border-line">
                      <td className="py-1.5 text-muted">{label}</td>
                      <td className="num py-1.5 text-right">
                        {!canWrite ? (
                          current === null ? "hidden" : String(current)
                        ) : kind === "bool" ? (
                          <input type="checkbox" checked={edited === undefined ? Boolean(current) : Boolean(edited)} onChange={(e) => setEdits({ ...edits, [key]: e.target.checked })} />
                        ) : (
                          <input
                            value={edited === undefined ? String(current ?? "") : String(edited)}
                            onChange={(e) => setEdits({ ...edits, [key]: e.target.value })}
                            className="w-28 rounded border border-line bg-bg px-2 py-0.5 text-right text-sm outline-none focus:border-accent"
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {saveSettings.error && <Notice tone="wrong">{(saveSettings.error as Error).message}</Notice>}
            <p className="mt-2 text-xs text-muted">
              Source: {String(status.data.settings._source)}.{canWrite ? " Edit and save; changes apply to new ideas and the next resolver run." : " Paste the write token to edit."}
            </p>
          </>
        ) : (
          <Notice>Loading…</Notice>
        )}
      </Panel>
    </div>
  );
}

function ServiceRow({ s }: { s: ServiceStatus }) {
  return (
    <li className="flex items-start gap-2">
      <span className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${s.ok ? "bg-right" : "bg-wrong"}`} />
      <div>
        <div className="font-medium">{s.name}</div>
        <div className="text-xs text-muted">
          {s.detail} · checked {fmtDateTime(s.checked_at)}
        </div>
      </div>
    </li>
  );
}
