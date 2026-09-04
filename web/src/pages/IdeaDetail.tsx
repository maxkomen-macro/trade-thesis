import { useParams } from "react-router-dom";
import { Notice, Panel } from "../components/Panel";

export function IdeaDetail() {
  const { id } = useParams();
  return (
    <div className="space-y-5">
      <h1 className="text-2xl">
        Idea <span className="num text-muted">#{id}</span>
      </h1>
      <Panel title="Detail">
        <Notice>Price chart with entry, target, stop, and window shading, plus the event timeline, arrive in Phase 2.</Notice>
      </Panel>
    </div>
  );
}
