import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export function Spinner({ label = "Caricamento…" }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-ink-muted">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-line-strong border-t-accent" aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-subtle ${className}`} aria-hidden="true" />;
}

/** Loading placeholder for lists; announces loading once to assistive tech. */
export function ListSkeleton({ rows = 4, label = "Caricamento in corso…" }: { rows?: number; label?: string }) {
  return (
    <div>
      <p role="status" className="sr-only">
        {label}
      </p>
      <div className="space-y-3">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="card space-y-2 p-4">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-3 w-2/3" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  children,
  icon = "inbox",
  action,
}: {
  title: string;
  children?: ReactNode;
  icon?: IconName;
  action?: ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center px-6 py-10 text-center">
      <span className="mb-3 grid h-10 w-10 place-items-center rounded-full bg-subtle text-ink-muted">
        <Icon name={icon} size={18} />
      </span>
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-ink-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, title = "Non riesco a caricare i dati" }: { error: unknown; onRetry?: () => void; title?: string }) {
  const message = error instanceof Error && error.message ? error.message : "Qualcosa è andato storto. Riprova tra poco.";
  return (
    <div role="alert" className="card flex flex-col items-start gap-3 border-danger/30 p-5 sm:flex-row sm:items-center">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-danger-soft text-danger-ink">
        <Icon name="alert" />
      </span>
      <div className="flex-1">
        <p className="font-medium text-danger-ink">{title}</p>
        <p className="text-sm text-ink-muted">{renderCode(message)}</p>
      </div>
      {onRetry && (
        <button type="button" className="btn-secondary" onClick={onRetry}>
          Riprova
        </button>
      )}
    </div>
  );
}

export function Notice({ tone = "info", title, children, icon }: { tone?: "info" | "warning"; title?: string; children: ReactNode; icon?: IconName }) {
  const cls = tone === "warning" ? "border-warning-ink/20 bg-warning-soft text-warning-ink" : "border-info-ink/15 bg-info-soft text-info-ink";
  return (
    <div className={`flex gap-3 rounded-xl border p-4 text-sm ${cls}`}>
      <Icon name={icon ?? (tone === "warning" ? "alert" : "info")} className="mt-0.5 shrink-0" />
      <div>
        {title && <p className="font-semibold">{title}</p>}
        <div className={title ? "mt-0.5" : ""}>{children}</div>
      </div>
    </div>
  );
}

/** Renders `backticked` parts of a message as code. */
export function renderCode(text: string) {
  return text.split(/(`[^`]+`)/).map((part, i) =>
    part.startsWith("`") && part.endsWith("`") ? (
      <code key={i} className="rounded bg-subtle px-1 font-mono text-ink">
        {part.slice(1, -1)}
      </code>
    ) : (
      part
    ),
  );
}
