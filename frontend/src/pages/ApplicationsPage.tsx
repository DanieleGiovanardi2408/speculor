import { useEffect, useId, useState, type FormEvent } from "react";
import { useAppStats, useApplications, useCreateApplication, usePatchApplication } from "../api/hooks";
import type { Application, ApplicationPatch, Stage, StatsRow } from "../api/types";
import { Badge } from "../components/Badge";
import { Dialog } from "../components/Dialog";
import { Icon } from "../components/Icon";
import { useCvVersions } from "../components/MarkSentDialog";
import { PageHeader, Section } from "../components/PageHeader";
import { EmptyState, ErrorState, ListSkeleton } from "../components/States";
import { useToast } from "../components/Toast";
import { formatDate, formatNumber, formatPercent, todayIso } from "../lib/format";
import { CHANNEL_OPTIONS, STAGES } from "../lib/labels";

const STAGE_TONE: Partial<Record<Stage, "success" | "danger" | "neutral" | "accent">> = {
  Offerta: "success",
  Rifiutata: "danger",
  "Nessuna risposta": "neutral",
  Ritirata: "neutral",
};

function usePatch(app: Application) {
  const patch = usePatchApplication();
  const toast = useToast();
  return (p: ApplicationPatch, msg = "Candidatura aggiornata.") =>
    // mutateAsync: the card may remount in another column before the request settles.
    patch.mutateAsync({ id: app.id, patch: p }).then(
      () => toast.success(msg),
      (e: Error) => toast.error(`Modifica non salvata: ${e.message}`),
    );
}

function BlurTextField({
  label,
  value,
  onSave,
  multiline = false,
  placeholder,
}: {
  label: string;
  value: string | null;
  onSave: (v: string | null) => void;
  multiline?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  const [v, setV] = useState(value ?? "");
  useEffect(() => setV(value ?? ""), [value]);
  const commit = () => {
    if (v !== (value ?? "")) onSave(v.trim() ? v : null);
  };
  return (
    <div>
      <label htmlFor={id} className="label">
        {label}
      </label>
      {multiline ? (
        <textarea
          id={id}
          className="field"
          rows={2}
          value={v}
          placeholder={placeholder}
          onChange={(e) => setV(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && (e.ctrlKey || e.metaKey) && commit()}
        />
      ) : (
        <input id={id} className="field" value={v} placeholder={placeholder} onChange={(e) => setV(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
      )}
    </div>
  );
}

function ApplicationCard({ app }: { app: Application }) {
  const [open, setOpen] = useState(false);
  const ids = { stage: useId(), panel: useId(), date: useId(), resp: useId() };
  const save = usePatch(app);
  return (
    <li className="card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium leading-snug">{app.role}</p>
          <p className="text-sm text-ink-muted">
            {app.company}
            {app.city && ` · ${app.city}`}
          </p>
        </div>
        {app.followup_due && (
          <Badge tone="warning" icon="clock">
            Follow-up
          </Badge>
        )}
      </div>
      <p className="mt-2 text-xs text-ink-muted">
        Inviata il {formatDate(app.date_sent)} · {app.channel} · {app.cv_version}
      </p>
      {app.next_step && (
        <p className="mt-2 flex items-start gap-1.5 text-sm">
          <Icon name="calendar" size={14} className="mt-0.5 shrink-0 text-ink-muted" />
          <span>
            {app.next_step}
            {app.next_step_date && <span className="text-ink-muted"> · {formatDate(app.next_step_date)}</span>}
          </span>
        </p>
      )}
      {app.responded && (
        <p className="mt-1 flex items-center gap-1 text-xs text-success-ink">
          <Icon name="check" size={12} /> Hanno risposto
        </p>
      )}

      <div className="mt-3">
        <label htmlFor={ids.stage} className="label">
          Fase
        </label>
        <select id={ids.stage} className="field" value={app.stage} onChange={(e) => save({ stage: e.target.value as Stage }, `Fase aggiornata: ${e.target.value}.`)}>
          {STAGES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>

      <button type="button" className="btn-ghost btn-sm mt-2 -ml-2" aria-expanded={open} aria-controls={ids.panel} onClick={() => setOpen((o) => !o)}>
        <Icon name="chevronDown" size={14} className={open ? "rotate-180" : ""} />
        {open ? "Nascondi dettagli" : "Prossimo passo e note"}
        <span className="sr-only"> per {app.company}</span>
      </button>
      <div id={ids.panel} hidden={!open} className="mt-2 space-y-3">
        {open && (
          <>
            <BlurTextField label="Prossimo passo" value={app.next_step} onSave={(v) => save({ next_step: v })} placeholder="es. Colloquio con il team" />
            <div>
              <label htmlFor={ids.date} className="label">
                Data del prossimo passo
              </label>
              <input id={ids.date} type="date" className="field" value={app.next_step_date ?? ""} onChange={(e) => save({ next_step_date: e.target.value || null })} />
            </div>
            <BlurTextField label="Note" value={app.notes} onSave={(v) => save({ notes: v })} multiline />
            <div className="flex items-center gap-2">
              <input
                id={ids.resp}
                type="checkbox"
                className="h-4 w-4 accent-[var(--color-accent)]"
                checked={app.responded}
                onChange={(e) => save({ responded: e.target.checked }, e.target.checked ? "Segnata come risposta ricevuta." : "Risposta rimossa.")}
              />
              <label htmlFor={ids.resp} className="text-sm">
                Hanno risposto
              </label>
            </div>
          </>
        )}
      </div>
    </li>
  );
}

function Pipeline({ apps }: { apps: Application[] }) {
  return (
    <div className="-mx-4 flex flex-col gap-6 px-4 lg:flex-row lg:gap-4 relative lg:overflow-x-auto lg:pb-4">
      {STAGES.map((stage) => {
        const items = apps.filter((a) => a.stage === stage);
        const id = `stage-${stage.replace(/[^a-z0-9]/gi, "-")}`;
        return (
          <section key={stage} aria-labelledby={id} className={`lg:w-72 lg:shrink-0 ${items.length === 0 ? "hidden lg:block" : ""}`}>
            <h3 id={id} className="mb-2 flex items-center gap-2 text-sm font-semibold">
              {STAGE_TONE[stage] ? <Badge tone={STAGE_TONE[stage]}>{stage}</Badge> : stage}
              <span className="rounded-full bg-subtle px-2 text-xs tabular-nums leading-5 text-ink-muted">
                {items.length}
                <span className="sr-only"> candidature</span>
              </span>
            </h3>
            {items.length === 0 ? (
              <p className="rounded-xl border border-dashed border-line p-4 text-center text-xs text-ink-muted">Nessuna</p>
            ) : (
              <ul className="space-y-3">
                {items.map((a) => (
                  <ApplicationCard key={a.id} app={a} />
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

/** interview_rate may be a 0–1 ratio or a 0–100 percentage; contract is ambiguous. */
const rate = (r: number) => formatPercent(r > 1 ? r / 100 : r);

function StatsTable({ caption, keyLabel, rows }: { caption: string; keyLabel: string; rows: StatsRow[] }) {
  return (
    <div className="card relative overflow-x-auto" role="region" aria-label={`Statistiche ${caption.toLowerCase()}`} tabIndex={0}>
      <table className="w-full min-w-[420px] text-sm">
        <caption className="px-4 pt-4 text-left text-sm font-semibold">{caption}</caption>
        <thead>
          <tr className="border-b border-line text-left text-xs text-ink-muted">
            <th scope="col" className="px-4 py-2 font-medium">
              {keyLabel}
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              Inviate
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              Risposte
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              Colloqui
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              Tasso colloqui
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={5} className="px-4 py-4 text-ink-muted">
                Nessun dato.
              </td>
            </tr>
          ) : (
            rows.map((r) => (
              <tr key={r.key} className="border-b border-line/70 last:border-0">
                <th scope="row" className="px-4 py-2 text-left font-medium">
                  {r.key}
                </th>
                <td className="px-4 py-2 text-right tabular-nums">{formatNumber(r.sent)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{formatNumber(r.responses)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{formatNumber(r.interviews)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{rate(r.interview_rate)}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function AddApplicationDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ids = { company: useId(), role: useId(), city: useId(), date: useId(), channel: useId(), cv: useId(), stage: useId(), notes: useId(), list: useId() };
  const empty = { company: "", role: "", city: "", date_sent: todayIso(), channel: CHANNEL_OPTIONS[0]!, cv_version: "", stage: "Inviata" as Stage, notes: "" };
  const [f, setF] = useState(empty);
  const [error, setError] = useState<string | null>(null);
  const cvs = useCvVersions();
  const create = useCreateApplication();
  const toast = useToast();

  useEffect(() => {
    if (open) {
      setF(empty);
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const set = (k: keyof typeof empty) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF((x) => ({ ...x, [k]: e.target.value }));

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!f.company.trim() || !f.role.trim() || !f.cv_version.trim()) {
      setError("Azienda, ruolo e versione CV sono obbligatori.");
      return;
    }
    create.mutate(
      {
        company: f.company.trim(),
        role: f.role.trim(),
        city: f.city.trim() || undefined,
        date_sent: f.date_sent,
        channel: f.channel,
        cv_version: f.cv_version.trim(),
        stage: f.stage,
        notes: f.notes.trim() || undefined,
      },
      {
        onSuccess: () => {
          toast.success("Candidatura aggiunta.");
          onClose();
        },
        onError: (err) => setError(err.message),
      },
    );
  }

  const req = <span aria-hidden="true" className="text-danger-ink"> *</span>;
  return (
    <Dialog open={open} onClose={onClose} title="Aggiungi candidatura" description="Per candidature inviate fuori da Radar.">
      <form onSubmit={submit} noValidate className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={ids.company} className="label">
            Azienda{req}
          </label>
          <input id={ids.company} className="field" value={f.company} onChange={set("company")} aria-required="true" data-autofocus />
        </div>
        <div>
          <label htmlFor={ids.role} className="label">
            Ruolo{req}
          </label>
          <input id={ids.role} className="field" value={f.role} onChange={set("role")} aria-required="true" />
        </div>
        <div>
          <label htmlFor={ids.city} className="label">
            Città
          </label>
          <input id={ids.city} className="field" value={f.city} onChange={set("city")} />
        </div>
        <div>
          <label htmlFor={ids.date} className="label">
            Data di invio{req}
          </label>
          <input id={ids.date} type="date" className="field" value={f.date_sent} onChange={set("date_sent")} aria-required="true" />
        </div>
        <div>
          <label htmlFor={ids.channel} className="label">
            Canale{req}
          </label>
          <select id={ids.channel} className="field" value={f.channel} onChange={set("channel")}>
            {CHANNEL_OPTIONS.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={ids.cv} className="label">
            Versione CV{req}
          </label>
          <input id={ids.cv} className="field" list={ids.list} value={f.cv_version} onChange={set("cv_version")} aria-required="true" />
          <datalist id={ids.list}>
            {cvs.map((v) => (
              <option key={v} value={v} />
            ))}
          </datalist>
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={ids.stage} className="label">
            Fase
          </label>
          <select id={ids.stage} className="field" value={f.stage} onChange={set("stage")}>
            {STAGES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={ids.notes} className="label">
            Note
          </label>
          <textarea id={ids.notes} className="field" rows={2} value={f.notes} onChange={set("notes")} />
        </div>
        {error && (
          <p role="alert" className="text-sm text-danger-ink sm:col-span-2">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-2 sm:col-span-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Annulla
          </button>
          <button type="submit" className="btn-primary" disabled={create.isPending}>
            {create.isPending ? "Salvataggio…" : "Aggiungi"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export function ApplicationsPage() {
  const apps = useApplications();
  const stats = useAppStats();
  const [addOpen, setAddOpen] = useState(false);
  const due = apps.data?.filter((a) => a.followup_due).length ?? 0;

  return (
    <>
      <PageHeader
        title="Candidature"
        subtitle={apps.data ? `${apps.data.length} candidature${due ? ` · ${due} con follow-up da fare` : ""}` : undefined}
        actions={
          <button type="button" className="btn-primary" onClick={() => setAddOpen(true)}>
            <Icon name="plus" size={14} /> Aggiungi candidatura
          </button>
        }
      />
      <Section title="Pipeline" id="pipeline-title">
        {apps.isPending ? (
          <ListSkeleton rows={3} label="Caricamento candidature…" />
        ) : apps.isError ? (
          <ErrorState error={apps.error} onRetry={() => apps.refetch()} />
        ) : apps.data.length === 0 ? (
          <EmptyState title="Nessuna candidatura ancora" icon="briefcase">
            Quando segni un annuncio come inviato, comparirà qui.
          </EmptyState>
        ) : (
          <Pipeline apps={apps.data} />
        )}
      </Section>

      <Section title="Statistiche" id="stats-title">
        {stats.isPending ? (
          <ListSkeleton rows={2} label="Caricamento statistiche…" />
        ) : stats.isError ? (
          <ErrorState error={stats.error} onRetry={() => stats.refetch()} />
        ) : (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <StatsTable caption="Per canale" keyLabel="Canale" rows={stats.data.by_channel} />
            <StatsTable caption="Per versione CV" keyLabel="Versione CV" rows={stats.data.by_cv} />
          </div>
        )}
      </Section>
      <AddApplicationDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </>
  );
}
