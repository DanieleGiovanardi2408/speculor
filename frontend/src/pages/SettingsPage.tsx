import { useEffect, useId, useState, type FormEvent } from "react";
import { usePutSettings, useRuns, useSettings } from "../api/hooks";
import { Icon } from "../components/Icon";
import { PageHeader, Section } from "../components/PageHeader";
import { RunSummaryView } from "../components/RunSummaryView";
import { EmptyState, ErrorState, ListSkeleton, Notice } from "../components/States";
import { Switch } from "../components/Switch";
import { useToast } from "../components/Toast";
import { sourceLabel } from "../lib/labels";

function GoalForm({ goal }: { goal: number }) {
  const id = useId();
  const [v, setV] = useState(String(goal));
  const put = usePutSettings();
  const toast = useToast();
  useEffect(() => setV(String(goal)), [goal]);
  function submit(e: FormEvent) {
    e.preventDefault();
    const n = Number(v);
    if (!Number.isInteger(n) || n < 1 || n > 100) {
      toast.error("Inserisci un numero intero tra 1 e 100.");
      return;
    }
    put.mutate({ weekly_goal: n }, { onSuccess: () => toast.success("Obiettivo salvato."), onError: (e) => toast.error(`Non salvato: ${e.message}`) });
  }
  return (
    <form onSubmit={submit} className="flex items-end gap-2">
      <div>
        <label htmlFor={id} className="label">
          Candidature a settimana
        </label>
        <input id={id} type="number" min={1} max={100} inputMode="numeric" className="field w-28" value={v} onChange={(e) => setV(e.target.value)} />
      </div>
      <button type="submit" className="btn-primary" disabled={String(goal) === v || put.isPending}>
        Salva
      </button>
    </form>
  );
}

export function SettingsPage() {
  const settings = useSettings();
  const runs = useRuns(10);
  const put = usePutSettings();
  const toast = useToast();

  return (
    <>
      <PageHeader title="Impostazioni" />
      {settings.isPending ? (
        <ListSkeleton rows={2} label="Caricamento impostazioni…" />
      ) : settings.isError ? (
        <ErrorState error={settings.error} onRetry={() => settings.refetch()} />
      ) : (
        <>
          <Section title="Obiettivo settimanale">
            <div className="card p-5">
              <GoalForm goal={settings.data.weekly_goal} />
            </div>
          </Section>
          <Section title="Fonti">
            <div className="card divide-y divide-line px-5">
              {Object.keys(settings.data.sources_enabled).length === 0 && <p className="py-4 text-sm text-ink-muted">Nessuna fonte configurata.</p>}
              {Object.entries(settings.data.sources_enabled).map(([name, enabled]) => (
                <Switch
                  key={name}
                  label={sourceLabel(name)}
                  description={enabled ? "Attiva: inclusa nella raccolta giornaliera" : "Disattivata"}
                  checked={enabled}
                  onChange={(v) =>
                    put.mutate(
                      { sources_enabled: { [name]: v } },
                      {
                        onSuccess: () => toast.success(`${sourceLabel(name)} ${v ? "attivata" : "disattivata"}.`),
                        onError: (e) => toast.error(`Non salvato: ${e.message}`),
                      },
                    )
                  }
                />
              ))}
            </div>
          </Section>
        </>
      )}

      <Section title="Regole dei filtri">
        <Notice icon="file">
          Le regole di filtro vivono in <code className="font-mono font-semibold">config/rules.yaml</code> e non si modificano da qui. Modifica il file, poi
          esegui <code className="font-mono font-semibold">radar refilter</code> per riapplicarle agli annunci esistenti.
        </Notice>
      </Section>

      <Section title="Ultime esecuzioni">
        {runs.isPending ? (
          <ListSkeleton rows={3} label="Caricamento esecuzioni…" />
        ) : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
        ) : runs.data.length === 0 ? (
          <EmptyState title="Nessuna esecuzione" icon="clock">
            Avvia <code className="font-mono">radar run</code> dal tuo computer.
          </EmptyState>
        ) : (
          <ol className="space-y-3">
            {runs.data.map((r) => (
              <li key={r.id} className="card p-4">
                <RunSummaryView run={r} />
              </li>
            ))}
          </ol>
        )}
      </Section>
      <p className="mt-8 flex items-center gap-1.5 text-xs text-ink-muted">
        <Icon name="lock" size={12} /> Radar non invia candidature né messaggi: tutto passa da te.
      </p>
    </>
  );
}
