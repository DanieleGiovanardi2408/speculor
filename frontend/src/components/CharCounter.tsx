/** Visible counter + polite announcement only when crossing the limit. */
export function CharCounter({ id, count, max, unit = "caratteri" }: { id?: string; count: number; max: number | null; unit?: string }) {
  if (max == null)
    return (
      <span id={id} className="text-xs text-ink-muted">
        {count} {unit}
      </span>
    );
  const over = count > max;
  return (
    <span id={id} className={`inline-flex items-center gap-1 text-xs ${over ? "font-semibold text-danger-ink" : "text-ink-muted"}`}>
      {over && <span aria-hidden="true">⚠</span>}
      {count} / {max} {unit}
      {over && <span>: oltre il limite di {count - max}</span>}
    </span>
  );
}
