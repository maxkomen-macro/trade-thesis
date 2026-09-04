import { Link } from "react-router-dom";
import { Notice, Panel, StatTile } from "../components/Panel";

export function Ledger() {
  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl">Ledger</h1>
          <p className="text-sm text-muted">Every idea, its machine-checked outcome, and paper P&amp;L against assigned capital.</p>
        </div>
        <Link to="/new" className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg">
          New thesis
        </Link>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatTile label="Ideas logged" value="–" />
        <StatTile label="Direction hit rate" value="–" />
        <StatTile label="Target hit rate" value="–" />
        <StatTile label="Hypothetical P&L" value="–" sub="paper portfolio" />
        <StatTile label="Open" value="–" tone="open" />
      </div>
      <Panel title="Ideas">
        <Notice>No ideas yet. The ledger table, progress bars, and resolver arrive in Phase 2.</Notice>
      </Panel>
      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Resolving soon">
          <Notice>Phase 2</Notice>
        </Panel>
        <Panel title="Hit rate by regime">
          <Notice>Phase 2, from the regime Radar reported when each idea was logged.</Notice>
        </Panel>
      </div>
    </div>
  );
}
