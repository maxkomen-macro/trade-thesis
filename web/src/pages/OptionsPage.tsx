import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { SystemStatus } from "../lib/types";
import { Notice, Panel } from "../components/Panel";

export function OptionsPage() {
  const { id } = useParams();
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<SystemStatus>("/api/status") });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl">Options expression</h1>
        <p className="text-sm text-muted">
          For idea <span className="num">#{id}</span> · <Link to={`/ideas/${id}`} className="underline">back to idea</Link>
        </p>
      </div>
      {status.isLoading && <Notice>Checking options data access…</Notice>}
      {status.data && !status.data.options_enabled && (
        <Panel title="Options selector is disabled">
          <p className="text-sm text-muted">
            <span className="font-mono text-xs">options_enabled</span> is off in Settings. It is only switched on after the EODHD
            options probe returns live chain data (run <span className="font-mono text-xs">make probe-eodhd</span>; results in
            <span className="font-mono text-xs"> docs/eodhd-probe.md</span>). Nothing on this page is estimated.
          </p>
        </Panel>
      )}
      {status.data?.options_enabled && (
        <Notice tone="accent">Options chain access confirmed (EODHD UnicornBay). The selector itself arrives in Phase 5.</Notice>
      )}
    </div>
  );
}
