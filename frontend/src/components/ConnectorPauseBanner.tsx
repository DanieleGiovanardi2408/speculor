import { useConnectorStatus, useResumeConnector } from "../api/hooks";
import type { ConnectorPause } from "../api/types";
import { formatDateTime } from "../lib/format";
import { SOURCE_LABEL } from "../lib/labels";
import { Icon } from "./Icon";
import { useToast } from "./Toast";

function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source;
}

function PausedRow({ source, pause }: { source: string; pause: ConnectorPause }) {
  const resume = useResumeConnector();
  const toast = useToast();
  const label = sourceLabel(source);
  const onResume = () =>
    resume.mutate(source, {
      onSuccess: () => toast.success(`${label}: ripresa. Riparte dalla prossima esecuzione.`),
      onError: (e) => toast.error(`Non riesco a riprendere ${label}. ${e.message}`),
    });
  return (
    <li className="flex flex-col gap-3 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-start">
      <div className="min-w-0 flex-1">
        <p className="font-medium">
          {label} <span className="font-normal text-warning-ink">in pausa</span>
        </p>
        <p className="text-sm">{pause.action}</p>
        <p className="mt-0.5 text-xs text-ink-muted">
          Motivo: {pause.reason}
          {pause.since && <> · dal {formatDateTime(pause.since)}</>}
        </p>
      </div>
      <button type="button" className="btn-secondary btn-sm shrink-0" onClick={onResume} disabled={resume.isPending}>
        <Icon name="undo" size={14} /> {resume.isPending ? "Riprendo…" : "Riprendi"}
        <span className="sr-only"> {label}</span>
      </button>
    </li>
  );
}

/**
 * Sources paused because they need the user (LinkedIn checkpoint, JobTeaser session expired,
 * IMAP login failed, ...). Generic: lists whatever GET /api/connectors/status returns.
 * Renders nothing when nothing is paused or the status can't be loaded.
 */
export function ConnectorPauseBanner() {
  const { data } = useConnectorStatus();
  const paused = Object.entries(data ?? {}).filter(([, v]) => v && v.paused);
  if (paused.length === 0) return null;
  return (
    <section aria-labelledby="paused-title" className="card border-warning-ink/30 bg-warning-soft/60 p-5">
      <h2 id="paused-title" className="mb-1 flex items-center gap-2 text-sm font-semibold text-warning-ink">
        <Icon name="alert" /> {paused.length === 1 ? "Una fonte è in pausa" : `${paused.length} fonti sono in pausa`}
      </h2>
      <p className="mb-3 text-sm text-ink-muted">
        Radar non le interroga finché non fai quello che chiedono e premi Riprendi. Nessun nuovo tentativo automatico.
      </p>
      <ul className="divide-y divide-line">
        {paused.map(([source, pause]) => (
          <PausedRow key={source} source={source} pause={pause} />
        ))}
      </ul>
    </section>
  );
}
