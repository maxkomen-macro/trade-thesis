import { Notice, Panel } from "../components/Panel";

export function Review() {
  return (
    <div className="space-y-5">
      <h1 className="text-2xl">Review</h1>
      <Panel title="Breakdowns">
        <Notice>By outcome, tag, regime, real vs paper, and rule type, plus the thesis-vs-option divergence table, arrive in Phase 4 and 6.</Notice>
      </Panel>
    </div>
  );
}
