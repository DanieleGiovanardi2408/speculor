import type { PostingSource, PostingSummary } from "../api/types";
import { sourceLabel } from "../lib/labels";
import { ExternalLink } from "./ExternalLink";

/** The job's sources, best link first; older servers send none (fallback: its own source). */
export function sourcesOf(p: PostingSummary): PostingSource[] {
  if (p.sources && p.sources.length > 0) return p.sources;
  return [{ source: p.source, url: p.url, via: null, label: sourceLabel(p.source), listed: true }];
}

function uniqueLabels(sources: PostingSource[]): string[] {
  return [...new Set(sources.map((s) => s.label))];
}

/** Compact source info for list rows: one label, or small chips when the job is on several sources. */
export function SourceChips({ posting }: { posting: PostingSummary }) {
  const labels = uniqueLabels(sourcesOf(posting));
  if (labels.length === 1) return <span className="hidden sm:inline">{labels[0]}</span>;
  return (
    <span className="hidden items-center gap-1 sm:inline-flex" aria-label={`Fonti: ${labels.join(", ")}`}>
      {labels.map((label) => (
        <span key={label} className="rounded border border-line bg-canvas px-1.5 text-[11px] leading-5 text-ink-muted">
          {label}
        </span>
      ))}
    </span>
  );
}

/** "Fonti: Sito aziendale, LinkedIn, Email alert" with a link per source. */
export function SourcesLine({ posting }: { posting: PostingSummary }) {
  const sources = sourcesOf(posting);
  const seen = new Set<string>();
  const items = sources.filter((s) => {
    const key = `${s.label}|${s.url}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return (
    <p className="text-sm">
      <span className="text-ink-muted">{items.length > 1 ? "Fonti:" : "Fonte:"}</span>{" "}
      {items.map((s, i) => (
        <span key={`${s.label}-${s.url}`}>
          {i > 0 && ", "}
          {s.url.startsWith("http") ? <ExternalLink href={s.url}>{s.label}</ExternalLink> : s.label}
          {!s.listed && <span className="text-ink-muted"> (non più in elenco)</span>}
        </span>
      ))}
    </p>
  );
}
