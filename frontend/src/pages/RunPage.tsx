import { Link, useParams } from "react-router-dom";
import { useRun, useRuns } from "../api/hooks";
import { Icon } from "../components/Icon";
import { PageHeader, Section } from "../components/PageHeader";
import { RunStatusBadge, RunSummaryView } from "../components/RunSummaryView";
import { EmptyState, ErrorState, ListSkeleton } from "../components/States";
import { formatDateTime } from "../lib/format";
import { Markdown } from "../lib/markdown";

export function RunPage() {
  const id = Number(useParams().id);
  const run = useRun(id);
  const runs = useRuns(10);

  return (
    <>
      <Link to="/" className="link mb-3 inline-flex min-h-11 items-center gap-1 text-sm sm:min-h-0">
        <Icon name="chevronRight" size={14} className="rotate-180" /> Torna a Oggi
      </Link>
      <PageHeader title={Number.isFinite(id) ? `Esecuzione n. ${id}` : "Esecuzione"} subtitle="Cosa ha fatto Radar in questo giro: fonti, errori e resoconto completo." />
      {run.isPending ? (
        <ListSkeleton rows={3} label="Caricamento esecuzione…" />
      ) : run.isError ? (
        <ErrorState error={run.error} onRetry={() => run.refetch()} title="Non riesco a caricare questa esecuzione" />
      ) : (
        <>
          <Section title="Riepilogo" id="run-summary">
            <div className="card p-5">
              <RunSummaryView run={run.data} />
            </div>
          </Section>
          <Section title="Resoconto completo" id="run-report">
            <div className="card p-5">
              {run.data.report_md ? (
                <Markdown source={run.data.report_md} headingOffset={2} />
              ) : (
                <p className="text-sm text-ink-muted">Questo giro non ha scritto un resoconto (succede se si è fermato prima della fine).</p>
              )}
            </div>
            <p className="mt-2 text-xs text-ink-muted">Il resoconto è privato: contiene titoli e nomi delle aziende.</p>
          </Section>
        </>
      )}
      <Section title="Altre esecuzioni" id="run-list">
        {runs.data && runs.data.length > 0 ? (
          <ul className="card divide-y divide-line">
            {runs.data.map((r) => (
              <li key={r.id}>
                <Link
                  to={`/esecuzioni/${r.id}`}
                  aria-current={r.id === id ? "page" : undefined}
                  className={`flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm hover:bg-subtle ${r.id === id ? "font-semibold" : ""}`}
                >
                  <span>n. {r.id}</span>
                  <RunStatusBadge status={r.status} />
                  <span className="text-ink-muted">{formatDateTime(r.started_at)}</span>
                  {r.id === id && <span className="text-xs text-ink-muted">(questa)</span>}
                </Link>
              </li>
            ))}
          </ul>
        ) : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
        ) : (
          !runs.isPending && <EmptyState title="Nessuna esecuzione registrata">Il prossimo giro è domattina alle 7.</EmptyState>
        )}
      </Section>
    </>
  );
}
