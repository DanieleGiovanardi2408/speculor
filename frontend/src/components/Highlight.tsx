/** Shows the text that triggered a filter rule, highlighted inside its context when available. */
export function EvidenceSnippet({ evidence, text, radius = 110 }: { evidence: string; text?: string | null; radius?: number }) {
  const mark = (
    <mark className="rounded-sm bg-warning-soft px-0.5 font-semibold text-ink underline decoration-warning-ink/60 decoration-2 underline-offset-2">
      <span className="sr-only">Testo che ha attivato la regola: </span>
      {evidence}
    </mark>
  );
  const at = text ? text.toLowerCase().indexOf(evidence.toLowerCase()) : -1;
  if (!text || at < 0) return <span>“{mark}”</span>;
  const start = Math.max(0, at - radius);
  const end = Math.min(text.length, at + evidence.length + radius);
  const before = text.slice(start, at).replace(/\s+/g, " ");
  const after = text.slice(at + evidence.length, end).replace(/\s+/g, " ");
  return (
    <span>
      {start > 0 && "…"}
      {before}
      <mark className="rounded-sm bg-warning-soft px-0.5 font-semibold text-ink underline decoration-warning-ink/60 decoration-2 underline-offset-2">
        <span className="sr-only">Testo che ha attivato la regola: </span>
        {text.slice(at, at + evidence.length)}
      </mark>
      {after}
      {end < text.length && "…"}
    </span>
  );
}
