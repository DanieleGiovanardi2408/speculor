import { useEffect, type ReactNode } from "react";

/** Page title (h1, focus target on route change) + document title. */
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · Radar`;
  }, [title]);
  return (
    <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 id="page-title" tabIndex={-1} className="text-2xl font-semibold tracking-tight focus:outline-none sm:text-[28px]">
          {title}
        </h1>
        {subtitle && <p className="mt-1 text-sm text-ink-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function Section({ title, children, actions, id }: { title: string; children: ReactNode; actions?: ReactNode; id?: string }) {
  const headingId = id ?? `sec-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section aria-labelledby={headingId} className="mb-8">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 id={headingId} className="text-base font-semibold">
          {title}
        </h2>
        {actions}
      </div>
      {children}
    </section>
  );
}
