import { Notice, Panel } from "../components/Panel";

export function NewThesis() {
  return (
    <div className="space-y-5">
      <h1 className="text-2xl">New thesis</h1>
      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Thesis">
          <textarea
            className="h-48 w-full resize-y rounded-md border border-line bg-bg p-3 text-sm outline-none focus:border-accent"
            placeholder="Plain English. Example: USO breaks 68.50 within six weeks as OPEC supply returns; wrong if it closes above 75."
            disabled
          />
          <div className="mt-3 flex gap-2">
            <button className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg opacity-50" disabled>
              Parse
            </button>
          </div>
        </Panel>
        <Panel title="Parsed fields">
          <Notice tone="accent">The Anthropic-backed parser and question flow arrive in Phase 3.</Notice>
        </Panel>
      </div>
    </div>
  );
}
