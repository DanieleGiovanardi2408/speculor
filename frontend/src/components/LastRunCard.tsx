import { Link } from "react-router-dom";
import { useUsage } from "../api/hooks";
import type { PostingSummary, RunSummary, UsageTotal } from "../api/types";
import { formatDateTime, formatNumber, plural } from "../lib/format";
import { sourceLabel } from "../lib/labels";
import { plainLlmSkipped, plainRunErrorLine } from "../lib/plain";
import { Icon } from "./Icon";
import { RunStatusBadge, parseRunStats } from "./RunSummaryView";
import { postingHref } from "../lib/actions";

const tokens = (t: UsageTotal) => t.input_tokens + t.output_tokens;

function UsageTable({ today, period }: { today: UsageTotal[]; period: UsageTotal[] }) {
  const models = period.map((t) => t.model);
  const find = (list: UsageTotal[], m: string) => list.find((t) => t.model === m);
  return (
    <div className="overflow-x-auto" role="region" aria-label="Utilizzo del modello linguistico" tabIndex={0}>
      <table className="w-full min-w-[300px] text-sm">
        <caption className="sr-only">Richieste e token per modello, oggi e negli ultimi 7 giorni</caption>
        <thead>
          <tr className="border-b border-line text-left text-xs text-ink-muted">
            <th scope="col" className="py-1.5 pr-3 font-medium">
              Modello
            </th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">
              Oggi
            </th>
            <th scope="col" className="py-1.5 text-right font-medium">
              7 giorni
            </th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => {
            const d = find(today, m);
            const w = find(period, m)!;
            return (
              <tr key={m} className="border-b border-line/70 last:border-0 align-top">
                <th scope="row" className="py-1.5 pr-3 text-left font-mono text-xs font-normal break-all">
                  {m}
                </th>
                <td className="py-1.5 pr-3 text-right tabular-nums">
                  {d ? (
                    <>
                      {plural(d.requests, "richiesta", "richieste")}
                      <span className="block text-xs text-ink-muted">{formatNumber(tokens(d))} token</span>
                    </>
                  ) : (
                    <span className="text-ink-muted">nessuna</span>
                  )}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {plural(w.requests, "richiesta", "richieste")}
                  <span className="block text-xs text-ink-muted">{formatNumber(tokens(w))} token</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Compact "Ultima esecuzione" for Oggi: sources, errors in plain Italian, closed, LLM usage. */
export function LastRunCard({ run, closed }: { run: RunSummary | null; closed: PostingSummary[] }) {
  const usage = useUsage(7);
  if (!run)
    return (
      <p className="text-sm text-ink-muted">
        Nessuna esecuzione registrata. Il primo giro parte domattina alle 7, oppure avvia <code className="font-mono text-ink">radar run</code> dal tuo computer.
      </p>
    );
  const s = parseRunStats(run.stats);
  const raw = (run.stats?.errors as unknown[] | undefined) ?? [];
  const errors = raw.map((e) => {
    if (e && typeof e === "object") {
      const o = e as Record<string, unknown>;
      return plainRunErrorLine(o.source ? String(o.source) : null, String(o.error ?? o.message ?? ""));
    }
    return plainRunErrorLine(null, String(e));
  });
  const totals = Object.fromEntries(s.totals.map((t) => [t.label, t.value]));

  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <RunStatusBadge status={run.status} />
        <span className="text-ink-muted">
          n. {run.id} · {formatDateTime(run.finished_at ?? run.started_at)}
        </span>
      </div>

      <p>
        <strong className="tabular-nums">{formatNumber(totals.nuovi ?? 0)}</strong> nuovi ·{" "}
        <strong className="tabular-nums">{formatNumber(totals.modificati ?? 0)}</strong> modificati ·{" "}
        <strong className="tabular-nums">{formatNumber(totals.chiusi ?? 0)}</strong> chiusi
        {totals.scartati != null && (
          <>
            {" "}
            · <strong className="tabular-nums">{formatNumber(totals.scartati)}</strong> scartati
          </>
        )}
      </p>

      {s.sources.length > 0 && (
        <div>
          <h3 className="eyebrow mb-1.5">Per fonte</h3>
          <ul className="flex flex-wrap gap-1.5">
            {s.sources.map((src) => {
              const failed = (src.counts.errors ?? 0) > 0;
              return (
                <li key={src.name} className={`rounded-md border px-2 py-1 text-xs ${failed ? "border-danger/30 bg-danger-soft text-danger-ink" : "border-line bg-canvas"}`}>
                  {failed && <Icon name="alert" size={12} className="mr-1 inline align-[-1px]" />}
                  <span className="font-medium">{sourceLabel(src.name)}</span>: {formatNumber(src.counts.fetched ?? 0)} visti, {formatNumber(src.counts.new ?? 0)} nuovi
                  {failed && <span>, errore</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {errors.length > 0 && (
        <div className="rounded-lg border border-danger/20 bg-danger-soft p-3 text-danger-ink">
          <p className="flex items-center gap-1.5 font-semibold">
            <Icon name="alert" size={14} /> {errors.length === 1 ? "1 problema" : `${errors.length} problemi`} durante il giro
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {s.llmSkipped && <p className="text-ink-muted">{plainLlmSkipped(s.llmSkipped)}</p>}

      <div>
        <h3 className="eyebrow mb-1.5">Chiusi da ieri</h3>
        {closed.length === 0 ? (
          <p className="text-ink-muted">Nessun annuncio chiuso.</p>
        ) : (
          <ul className="space-y-1">
            {closed.map((p) => (
              <li key={p.id} className="truncate">
                <Icon name="lock" size={12} className="mr-1 inline align-[-1px] text-ink-muted" />
                <Link to={postingHref(p)} className="hover:underline">
                  {p.title}
                </Link>
                <span className="text-ink-muted"> · {p.company_name}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="eyebrow mb-1.5">Modello linguistico</h3>
        {usage.isPending ? (
          <p className="text-ink-muted">Caricamento utilizzo…</p>
        ) : usage.isError ? (
          <p className="text-ink-muted">Non riesco a leggere l'utilizzo adesso.</p>
        ) : !usage.data ? (
          <p className="text-ink-muted">
            {s.tokens.length > 0 ? `Questo giro: ${s.tokens.map((t) => `${formatNumber(t.value)} token ${t.label}`).join(", ")}.` : "Nessun dato di utilizzo."}
          </p>
        ) : usage.data.period.length === 0 ? (
          <p className="text-ink-muted">Nessuna richiesta negli ultimi 7 giorni.</p>
        ) : (
          <UsageTable today={usage.data.today} period={usage.data.period} />
        )}
      </div>

      <Link to={`/esecuzioni/${run.id}`} className="link inline-flex min-h-11 items-center gap-1 sm:min-h-0">
        Leggi il resoconto completo <Icon name="chevronRight" size={14} />
      </Link>
    </div>
  );
}
