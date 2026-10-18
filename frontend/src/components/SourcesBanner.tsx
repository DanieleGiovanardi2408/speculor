import { Link } from "react-router-dom";
import { useSources } from "../api/hooks";
import { Icon } from "./Icon";

/**
 * Oggi: sources with errors in the last run (paused ones have their own banner with Riprendi),
 * linking to Fonti for the details. Renders nothing when all is well or on load errors.
 */
export function SourcesBanner() {
  const { data } = useSources();
  const failing = data?.sources.filter((s) => s.state === "errors") ?? [];
  if (failing.length === 0) return null;
  return (
    <section aria-labelledby="sources-failing-title" className="card border-danger/30 bg-danger-soft/60 p-5">
      <h2 id="sources-failing-title" className="mb-1 flex items-center gap-2 text-sm font-semibold text-danger-ink">
        <Icon name="alert" /> {failing.length === 1 ? "Una fonte ha avuto errori" : `${failing.length} fonti hanno avuto errori`} nell'ultimo giro
      </h2>
      <p className="mb-3 text-sm">{failing.map((s) => s.label).join(", ")}.</p>
      <Link to="/fonti" className="btn-secondary btn-sm">
        Apri Fonti: cosa è successo e cosa fare <Icon name="chevronRight" size={14} />
      </Link>
    </section>
  );
}
