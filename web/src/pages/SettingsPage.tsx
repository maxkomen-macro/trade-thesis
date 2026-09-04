import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, getWriteToken, setWriteToken } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import type { ServiceStatus, SystemStatus } from "../lib/types";
import { Notice, Panel } from "../components/Panel";

export function SettingsPage() {
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<SystemStatus>("/api/status") });
  const [token, setToken] = useState(getWriteToken());
  const [saved, setSaved] = useState(false);

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
      <Panel title="Defaults">
        {status.data ? (
          <>
            <table className="w-full text-sm">
              <tbody>
                {Object.entries(status.data.settings)
                  .filter(([k]) => !k.startsWith("_"))
                  .map(([k, v]) => (
                    <tr key={k} className="border-t border-line">
                      <td className="py-1.5 text-muted">{k.replaceAll("_", " ")}</td>
                      <td className="num py-1.5 text-right">{String(v)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-muted">Source: {String(status.data.settings._source)}. Editing arrives in Phase 4.</p>
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
