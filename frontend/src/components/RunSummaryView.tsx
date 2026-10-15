import type { RunSummary } from "../api/types";
import { formatDateTime, formatNumber } from "../lib/format";
import { sourceLabel } from "../lib/labels";
import { Badge, type Tone } from "./Badge";
import type { IconName } from "./Icon";

const RUN_STATUS: Record<RunSummary["status"], { label: string; tone: Tone; icon: IconName }> = {
  running: { label: "In corso", tone: "info", icon: "clock" },
  ok: { label: "Completata", tone: "success", icon: "check" },
  partial: { label: "Parziale", tone: "warning", icon: "alert" },
  failed: { label: "Fallita", tone: "danger", icon: "x" },
};

export function RunStatusBadge({ status }: { status: RunSummary["status"] }) {
  const m = RUN_STATUS[status];
  return (
    <Badge tone={m.tone} icon={m.icon}>
      {m.label}
    </Badge>
  );
}

// `stats` is free-form in the contract; parse defensively.
interface ParsedStats {
  sources: { name: string; counts: Record<string, number> }[];
  errors: string[];
  tokens: { label: string; value: number }[];
  totals: { label: string; value: number }[];
  llmSkipped?: string;
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

const COUNT_LABEL: Record<string, string> = {
  fetched: "trovati",
  seen: "visti",
  new: "nuovi",
  changed: "modificati",
  closed: "chiusi",
  reopened: "riaperti",
  errors: "errori",
  rejected: "scartati",
  flagged: "segnalati",
  filtered: "filtrati",
  scored: "valutati",
  boards: "bacheche",
  closed_by_absence: "chiusi (spariti)",
  closed_by_check: "chiusi (verifica)",
};
export const countLabel = (k: string) => COUNT_LABEL[k] ?? k.replace(/_/g, " ");

export function parseRunStats(stats: Record<string, unknown> | null): ParsedStats {
  const out: ParsedStats = { sources: [], errors: [], tokens: [], totals: [] };
  if (!stats) return out;
  for (const [key, value] of Object.entries(stats)) {
    if ((key === "sources" || key === "per_source" || key === "by_source") && isObj(value)) {
      for (const [name, counts] of Object.entries(value)) {
        if (isObj(counts)) {
          const nums = Object.fromEntries(Object.entries(counts).filter(([, n]) => typeof n === "number")) as Record<string, number>;
          out.sources.push({ name, counts: nums });
          if (Array.isArray(counts.errors)) counts.errors.forEach((e) => out.errors.push(`${name}: ${String(e)}`));
          else if (typeof counts.error === "string") out.errors.push(`${name}: ${counts.error}`);
        } else if (typeof counts === "number") {
          out.sources.push({ name, counts: { trovati: counts } });
        }
      }
    } else if (key === "errors" && Array.isArray(value)) {
      value.forEach((e) => {
        if (!isObj(e)) return out.errors.push(String(e));
        const msg = String(e.error ?? e.message ?? JSON.stringify(e));
        out.errors.push(e.source ? `${String(e.source)}: ${msg}` : msg);
      });
    } else if (key === "llm" && isObj(value)) {
      // Backend shape: { scoring: {input_tokens, output_tokens, ...}, packs: {...}, skipped?: string }
      let input = 0;
      let output = 0;
      for (const part of Object.values(value)) {
        if (!isObj(part)) continue;
        if (typeof part.input_tokens === "number") input += part.input_tokens;
        if (typeof part.output_tokens === "number") output += part.output_tokens;
      }
      if (input || output) out.tokens.push({ label: "input", value: input }, { label: "output", value: output });
      if (typeof value.skipped === "string") out.llmSkipped = value.skipped;
    } else if (/token/i.test(key)) {
      if (typeof value === "number") out.tokens.push({ label: "totale", value });
      else if (isObj(value))
        Object.entries(value).forEach(([k, v]) => typeof v === "number" && out.tokens.push({ label: k === "input" ? "input" : k === "output" ? "output" : k, value: v }));
    } else if (typeof value === "number") {
      out.totals.push({ label: countLabel(key), value });
    }
  }
  return out;
}

export function RunSummaryView({ run, compact = false }: { run: RunSummary; compact?: boolean }) {
  const s = parseRunStats(run.stats);
  const columns = Array.from(new Set(s.sources.flatMap((x) => Object.keys(x.counts))));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <RunStatusBadge status={run.status} />
        <span className="text-ink-muted">
          Esecuzione n. {run.id} · avviata {formatDateTime(run.started_at)}
          {run.finished_at && <> · terminata {formatDateTime(run.finished_at)}</>}
        </span>
      </div>

      {s.totals.length > 0 && (
        <ul className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          {s.totals.map((t) => (
            <li key={t.label}>
              <span className="font-semibold tabular-nums">{formatNumber(t.value)}</span> <span className="text-ink-muted">{t.label}</span>
            </li>
          ))}
        </ul>
      )}

      {!compact && s.sources.length > 0 && (
        <div className="relative overflow-x-auto" role="region" aria-label={`Conteggi per fonte, esecuzione ${run.id}`} tabIndex={0}>
          <table className="w-full min-w-[320px] text-sm">
            <caption className="sr-only">Conteggi per fonte</caption>
            <thead>
              <tr className="border-b border-line text-left text-xs text-ink-muted">
                <th scope="col" className="py-2 pr-4 font-medium">
                  Fonte
                </th>
                {columns.map((c) => (
                  <th key={c} scope="col" className="py-2 pr-4 text-right font-medium capitalize">
                    {countLabel(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {s.sources.map((src) => (
                <tr key={src.name} className="border-b border-line/70 last:border-0">
                  <th scope="row" className="py-2 pr-4 text-left font-medium">
                    {sourceLabel(src.name)}
                  </th>
                  {columns.map((c) => (
                    <td key={c} className="py-2 pr-4 text-right tabular-nums">
                      {src.counts[c] != null ? formatNumber(src.counts[c]!) : "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {s.errors.length > 0 && (
        <div className="rounded-lg border border-danger/20 bg-danger-soft p-3 text-sm text-danger-ink">
          <p className="font-semibold">
            {s.errors.length === 1 ? "1 errore" : `${s.errors.length} errori`} durante l'esecuzione
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {s.errors.map((e, i) => (
              <li key={i} className="break-words">
                {e}
              </li>
            ))}
          </ul>
        </div>
      )}

      {s.llmSkipped && <p className="text-xs text-ink-muted">LLM saltato: {s.llmSkipped}</p>}
      {s.tokens.length > 0 && (
        <p className="text-xs text-ink-muted">
          Token LLM: {s.tokens.map((t) => `${formatNumber(t.value)} ${t.label}`).join(" · ")}
        </p>
      )}
    </div>
  );
}
