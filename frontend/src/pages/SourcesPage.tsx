import { Link } from "react-router-dom";
import { useResumeConnector, useSources } from "../api/hooks";
import type { SourceBoards, SourceCard, SourceRunInfo, SourceState, SourcesOverview } from "../api/types";
import { Badge, type Tone } from "../components/Badge";
import { Icon, type IconName } from "../components/Icon";
import { PageHeader, Section } from "../components/PageHeader";
import { ErrorState, ListSkeleton, renderCode } from "../components/States";
import { useToast } from "../components/Toast";
import { formatDateTime, formatNumber, formatShortDate, plural } from "../lib/format";

const STATE_META: Record<SourceState, { label: string; tone: Tone; icon: IconName }> = {
  ok: { label: "Funziona", tone: "success", icon: "check" },
  errors: { label: "Errori", tone: "danger", icon: "alert" },
  paused: { label: "In pausa", tone: "warning", icon: "clock" },
  disabled: { label: "Spenta", tone: "neutral", icon: "minus" },
};
const STATE_ORDER: SourceState[] = ["paused", "errors", "ok", "disabled"];

const STEP_LABEL: Record<string, string> = {
  collect: "raccolta",
  closed_check: "controllo chiusi",
  describe: "descrizioni tracker",
  filters: "filtri",
  rank: "somiglianza",
  scoring: "valutazione",
  packs: "pacchetti",
  backlog: "arretrato tracker",
  reverify: "riverifica",
  contacts: "contatti",
};

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

const weekday = new Intl.DateTimeFormat("it-IT", { weekday: "short" });

/** Seven bars, one per day. The numbers are in a visually hidden list for screen readers. */
function MiniChart({ data, label }: { data: { day: string; n: number }[]; label: string }) {
  const max = Math.max(1, ...data.map((d) => d.n));
  const total = data.reduce((a, d) => a + d.n, 0);
  return (
    <figure className="mt-3">
      <figcaption className="mb-1 flex items-baseline justify-between text-xs text-ink-muted">
        <span>{label}, ultimi 7 giorni</span>
        <span className="tabular-nums">{formatNumber(total)} in tutto</span>
      </figcaption>
      <div className="flex h-12 items-end gap-1" aria-hidden="true">
        {data.map((d) => (
          <div key={d.day} className="flex h-full flex-1 flex-col items-center justify-end gap-0.5">
            <span className="text-[10px] leading-none tabular-nums text-ink-muted">{d.n || ""}</span>
            <div
              className={`w-full rounded-sm ${d.n ? "bg-accent" : "bg-line"}`}
              style={{ height: d.n ? `${Math.max(8, (d.n / max) * 75)}%` : "2px" }}
            />
          </div>
        ))}
      </div>
      <div className="mt-0.5 flex gap-1 text-[10px] text-ink-faint" aria-hidden="true">
        {data.map((d) => (
          <span key={d.day} className="flex-1 text-center">
            {weekday.format(new Date(`${d.day}T12:00:00`)).replace(".", "")}
          </span>
        ))}
      </div>
      <ul className="sr-only">
        {data.map((d) => (
          <li key={d.day}>
            {formatShortDate(d.day)}: {d.n}
          </li>
        ))}
      </ul>
    </figure>
  );
}

function Boards({ boards, label }: { boards: SourceBoards; label: string }) {
  const failing = boards.failing.length;
  return (
    <div className="mt-3 text-sm">
      <p>
        <strong className="tabular-nums">{formatNumber(boards.configured)}</strong> bacheche
        {boards.working != null && (
          <>
            {" · "}
            <span className="tabular-nums">{formatNumber(boards.working)}</span> funzionano
          </>
        )}
        {boards.detail && (
          <>
            {" · "}
            <span className="tabular-nums">{formatNumber(boards.empty.length)}</span> senza annunci
          </>
        )}
        {" · "}
        <span className={`tabular-nums ${failing ? "font-semibold text-danger-ink" : ""}`}>{formatNumber(failing)}</span> con errori
      </p>
      {!boards.detail && <p className="mt-0.5 text-xs text-ink-muted">Il dettaglio per bacheca arriva dalla prossima esecuzione.</p>}
      {failing > 0 && (
        <details className="mt-2 rounded-lg border border-danger/20 bg-danger-soft/50 px-3 py-2">
          <summary className="cursor-pointer font-medium text-danger-ink">
            Bacheche con errori ({failing})<span className="sr-only"> di {label}</span>
          </summary>
          <ul className="mt-2 space-y-2">
            {boards.failing.map((b, i) => (
              <li key={i}>
                <p className="font-medium">{b.company ?? "Bacheca senza nome"}</p>
                <p className="text-ink-muted">{b.problem}</p>
                {b.action && <p className="text-xs">Cosa fare: {renderCode(b.action)}</p>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {boards.empty.length > 0 && (
        <details className="mt-2 rounded-lg border border-line px-3 py-2">
          <summary className="cursor-pointer font-medium">
            Senza annunci all'ultimo giro ({boards.empty.length})<span className="sr-only"> di {label}</span>
          </summary>
          <p className="mt-1 text-xs text-ink-muted">Rispondono, ma oggi non hanno annunci che passano il filtro sui titoli.</p>
          <ul className="mt-1 columns-1 text-ink-muted sm:columns-2">
            {boards.empty.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ResumeButton({ source }: { source: SourceCard }) {
  const resume = useResumeConnector();
  const toast = useToast();
  return (
    <button
      type="button"
      className="btn-secondary btn-sm"
      disabled={resume.isPending}
      onClick={() =>
        resume.mutate(source.key, {
          onSuccess: () => toast.success(`${source.label}: ripresa. Riparte dalla prossima esecuzione.`),
          onError: (e) => toast.error(`Non riesco a riprendere ${source.label}. ${e.message}`),
        })
      }
    >
      <Icon name="undo" size={14} /> {resume.isPending ? "Riprendo…" : "Riprendi"}
      <span className="sr-only"> {source.label}</span>
    </button>
  );
}

function SourceCardView({ source }: { source: SourceCard }) {
  const meta = STATE_META[source.state];
  const id = `src-${source.key}`;
  const run = source.last_run;
  const pass = source.last_pass;
  return (
    <section aria-labelledby={id} className={`card flex flex-col p-5 ${source.state === "paused" || source.state === "errors" ? "border-warning-ink/30" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 id={id} className="font-semibold">
          {source.label}
        </h3>
        <Badge tone={meta.tone} icon={meta.icon}>
          {meta.label}
        </Badge>
      </div>

      {source.problem && (
        <div className={`mt-3 rounded-lg p-3 text-sm ${source.state === "disabled" ? "bg-subtle" : source.state === "paused" ? "bg-warning-soft text-warning-ink" : "bg-danger-soft text-danger-ink"}`}>
          <p className="font-medium">{renderCode(source.problem)}</p>
          {source.action && (
            <p className="mt-1">
              <span className="font-semibold">Cosa fare:</span> {renderCode(source.action)}
            </p>
          )}
          {source.paused_since && <p className="mt-1 text-xs opacity-80">In pausa dal {formatDateTime(source.paused_since)}</p>}
        </div>
      )}
      {source.resumable && (
        <div className="mt-3">
          <ResumeButton source={source} />
        </div>
      )}

      {source.key === "extension" ? (
        <p className="mt-3 text-sm">
          {pass ? (
            <>
              Ultimo «Salva in Radar»: <strong>{formatDateTime(pass.at)}</strong>, {plural(pass.received, "offerta", "offerte")} ({plural(pass.new, "nuova", "nuove")}).
            </>
          ) : (
            <span className="text-ink-muted">Nessun passaggio con l'estensione finora.</span>
          )}
        </p>
      ) : source.kind === "llm" ? (
        <p className="mt-3 text-sm text-ink-muted">{run ? <>Ultima ricerca nel giro del {formatDateTime(run.started_at)}.</> : "Nessuna ricerca ancora."}</p>
      ) : (
        <p className="mt-3 text-sm">
          {run ? (
            <>
              <span className="text-ink-muted">Ultimo giro {formatDateTime(run.started_at)}:</span>{" "}
              <span className="tabular-nums">{formatNumber(source.fetched ?? 0)}</span> letti,{" "}
              <span className="tabular-nums">{formatNumber(source.new ?? 0)}</span> nuovi
              {source.closed != null && (
                <>
                  , <span className="tabular-nums">{formatNumber(source.closed)}</span> chiusi
                </>
              )}
              {source.seconds != null && <span className="text-ink-muted"> · {duration(source.seconds)}</span>}
            </>
          ) : (
            <span className="text-ink-muted">Non ancora usata in un giro.</span>
          )}
        </p>
      )}

      {source.boards && <Boards boards={source.boards} label={source.label} />}
      {source.chart ? (
        <MiniChart data={source.chart} label={source.kind === "llm" ? "Contatti trovati" : "Annunci nuovi"} />
      ) : (
        source.key === "jobteaser" && <p className="mt-3 text-xs text-ink-muted">Questa fonte aggiunge la descrizione alle offerte già salvate, non annunci nuovi.</p>
      )}
    </section>
  );
}

function Meter({ label, used, limit, unit, note }: { label: string; used: number; limit: number | null; unit: string; note?: string }) {
  const ratio = limit ? Math.min(1, used / limit) : 0;
  const high = ratio >= 0.9;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="font-medium break-all">{label}</span>
        <span className="shrink-0 tabular-nums">
          {formatNumber(used)}
          {limit != null && <> / {formatNumber(limit)}</>} {unit}
        </span>
      </div>
      {limit != null && (
        <div
          role="meter"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={limit}
          aria-valuenow={used}
          aria-valuetext={`${formatNumber(used)} su ${formatNumber(limit)} ${unit}`}
          className="mt-1 h-2 overflow-hidden rounded-full bg-subtle"
        >
          <div className={`h-full rounded-full ${high ? "bg-danger" : "bg-accent"}`} style={{ width: `${ratio * 100}%` }} />
        </div>
      )}
      {note && <p className="mt-0.5 text-xs text-ink-muted">{note}</p>}
    </div>
  );
}

function Quotas({ quotas }: { quotas: SourcesOverview["quotas"] }) {
  const c = quotas.contacts;
  return (
    <div className="card grid gap-5 p-5 sm:grid-cols-2 lg:grid-cols-3">
      <div className="space-y-3">
        <h3 className="eyebrow">LinkedIn oggi</h3>
        {quotas.linkedin ? (
          <Meter
            label="Chiamate di linkedin-cli"
            used={quotas.linkedin.used}
            limit={quotas.linkedin.limit}
            unit="chiamate"
            note={`Radar ne usa al massimo ${quotas.linkedin.limit}; linkedin-cli ne permette ${quotas.linkedin.cli_limit}.`}
          />
        ) : (
          <p className="text-sm text-ink-muted">linkedin-cli non disponibile: nessun dato.</p>
        )}
      </div>
      <div className="space-y-3">
        <h3 className="eyebrow">Groq oggi (token per modello)</h3>
        {quotas.groq.length === 0 ? (
          <p className="text-sm text-ink-muted">Nessuna richiesta oggi.</p>
        ) : (
          quotas.groq.map((g) => <Meter key={g.model} label={g.model} used={g.used} limit={g.limit} unit="token" />)
        )}
        <p className="text-xs text-ink-muted">Limite giornaliero del piano gratuito, giorno UTC.</p>
      </div>
      <div className="space-y-3">
        <h3 className="eyebrow">Ricerca contatti</h3>
        <Meter
          label="Spesa stimata questo mese"
          used={Number(c.month_cost_eur.toFixed(2))}
          limit={c.monthly_cap_eur}
          unit="€"
        />
        <Meter label="Ricerche oggi" used={c.calls_today} limit={c.daily_call_cap} unit="chiamate" />
      </div>
    </div>
  );
}

function RunBox({ run }: { run: SourceRunInfo }) {
  const slept = run.wall_seconds != null && run.awake_seconds != null && run.wall_seconds - run.awake_seconds > 600;
  const steps = Object.entries(run.timings ?? {}).filter(([, s]) => s >= 1);
  return (
    <div className="card p-5 text-sm">
      <p>
        Esecuzione n. {run.id} del {formatDateTime(run.started_at)}: durata <strong>{duration(run.wall_seconds)}</strong>
        {run.awake_seconds != null && (
          <>
            , lavoro effettivo <strong>{duration(run.awake_seconds)}</strong>
          </>
        )}
        .
      </p>
      {slept && (
        <p className="mt-1 text-ink-muted">
          La differenza è il tempo in cui il Mac era in stop: il giro avanza solo quando è sveglio. Lascialo collegato alla corrente prima delle 7.
        </p>
      )}
      {steps.length > 0 && (
        <p className="mt-1 text-ink-muted">
          {steps.map(([k, s]) => `${STEP_LABEL[k] ?? k} ${duration(s)}`).join(" · ")}
        </p>
      )}
      <Link to={`/esecuzioni/${run.id}`} className="link mt-2 inline-flex min-h-11 items-center gap-1 sm:min-h-0">
        Leggi il resoconto completo <Icon name="chevronRight" size={14} />
      </Link>
    </div>
  );
}

export function SourcesPage() {
  const { data, isPending, isError, error, refetch } = useSources();
  const sorted = data ? [...data.sources].sort((a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state)) : [];
  const attention = data?.sources.filter((s) => s.state === "paused" || s.state === "errors") ?? [];
  return (
    <div>
      <PageHeader
        title="Fonti"
        subtitle={
          data
            ? attention.length
              ? `${plural(attention.length, "fonte richiede", "fonti richiedono")} attenzione.`
              : "Tutte le fonti attive funzionano."
            : "Da dove arrivano gli annunci e come sta ognuna."
        }
      />
      {isPending ? (
        <ListSkeleton rows={4} label="Carico lo stato delle fonti…" />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <>
          {data.last_run && (
            <Section title="Ultima esecuzione">
              <RunBox run={data.last_run} />
            </Section>
          )}
          <Section title="Quote di oggi">
            <Quotas quotas={data.quotas} />
          </Section>
          <Section title="Fonti">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {sorted.map((s) => (
                <SourceCardView key={s.key} source={s} />
              ))}
            </div>
          </Section>
        </>
      )}
    </div>
  );
}
