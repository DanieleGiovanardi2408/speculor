import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { useAddPosting, usePostings, useSettings } from "../api/hooks";
import type { PostingSort, PostingsQuery } from "../api/types";
import { Dialog } from "../components/Dialog";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { PostingRow } from "../components/PostingRow";
import { EmptyState, ErrorState, ListSkeleton } from "../components/States";
import { useToast } from "../components/Toast";
import { sourceLabel } from "../lib/labels";

const STATE_OPTIONS: { value: string; label: string; query: Partial<PostingsQuery> }[] = [
  { value: "todo", label: "Da valutare", query: { user_status: "todo" } },
  { value: "sent", label: "Candidatura inviata", query: { user_status: "sent" } },
  { value: "skipped", label: "Saltati", query: { user_status: "skipped" } },
  { value: "open", label: "Aperti", query: { status: "open" } },
  { value: "closed", label: "Chiusi", query: { status: "closed" } },
];

const SORT_OPTIONS: { value: PostingSort; label: string }[] = [
  { value: "priority", label: "Priorità" },
  { value: "deadline", label: "Scadenza" },
  { value: "company", label: "Azienda" },
  { value: "first_seen", label: "Più recenti" },
];

/** URL search params <-> API query. Italian param names keep shared links readable. */
function toQuery(sp: URLSearchParams): PostingsQuery {
  const state = STATE_OPTIONS.find((s) => s.value === sp.get("stato"));
  const sort = sp.get("ordina") as PostingSort | null;
  return {
    priority: sp.get("priorita") ?? undefined,
    zone: sp.get("zona") ?? undefined,
    type: sp.get("tipo") ?? undefined,
    source: sp.get("fonte") ?? undefined,
    q: sp.get("q") ?? undefined,
    sort: sort && SORT_OPTIONS.some((o) => o.value === sort) ? sort : "priority",
    include_rejected: sp.get("scartati") === "1" ? true : undefined,
    ...(state?.query ?? {}),
  };
}

function AddFromUrl() {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const add = useAddPosting();
  const toast = useToast();

  function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const u = new URL(url.trim());
      if (!/^https?:$/.test(u.protocol)) throw new Error();
    } catch {
      setError("Inserisci un indirizzo completo, che inizi con https://");
      return;
    }
    add.mutate(url.trim(), {
      onSuccess: (p) => {
        toast.success(`Annuncio aggiunto: ${p.title}`);
        setOpen(false);
        setUrl("");
      },
      onError: (err) => setError(err.message),
    });
  }

  return (
    <>
      <button type="button" className="btn-secondary" onClick={() => setOpen(true)}>
        <Icon name="plus" size={14} /> Aggiungi da URL
      </button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Aggiungi annuncio da URL"
        description="Incolla il link di un annuncio trovato altrove: Radar lo scarica, lo filtra e lo valuta come gli altri."
      >
        <form onSubmit={submit} noValidate className="space-y-4">
          <div>
            <label htmlFor={id} className="label">
              Indirizzo dell'annuncio
            </label>
            <input
              id={id}
              type="url"
              inputMode="url"
              className="field"
              placeholder="https://…"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setError(null);
              }}
              aria-invalid={!!error}
              aria-describedby={error ? `${id}-err` : undefined}
              data-autofocus
              required
            />
            {error && (
              <p id={`${id}-err`} role="alert" className="mt-1 text-sm text-danger-ink">
                {error}
              </p>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
              Annulla
            </button>
            <button type="submit" className="btn-primary" disabled={add.isPending}>
              {add.isPending ? "Aggiunta in corso…" : "Aggiungi"}
            </button>
          </div>
        </form>
      </Dialog>
    </>
  );
}

function FilterSelect({
  label,
  param,
  options,
  sp,
  update,
  allLabel = "Tutti",
}: {
  label: string;
  param: string;
  options: { value: string; label: string }[];
  sp: URLSearchParams;
  update: (k: string, v: string | null) => void;
  allLabel?: string;
}) {
  const id = useId();
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="label">
        {label}
      </label>
      <select id={id} className="field" value={sp.get(param) ?? ""} onChange={(e) => update(param, e.target.value || null)}>
        <option value="">{allLabel}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function PostingsPage() {
  const [sp, setSp] = useSearchParams();
  const query = useMemo(() => toQuery(sp), [sp]);
  const { data, isPending, isError, error, refetch, isFetching } = usePostings(query);
  // Unfiltered list to derive type/source options.
  const all = usePostings({ include_rejected: true });
  const settings = useSettings();
  const openId = Number(sp.get("open"));
  const searchId = useId();
  const sortId = useId();
  const rejectedId = useId();
  const filtersId = useId();
  const [filtersOpen, setFiltersOpen] = useState(false);

  const [search, setSearch] = useState(sp.get("q") ?? "");
  useEffect(() => {
    const t = window.setTimeout(() => {
      if ((sp.get("q") ?? "") !== search) update("q", search.trim() || null);
    }, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  function update(key: string, value: string | null) {
    setSp(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value == null || value === "") next.delete(key);
        else next.set(key, value);
        next.delete("open");
        return next;
      },
      { replace: true },
    );
  }

  const typeOptions = useMemo(() => {
    const set = new Set<string>();
    all.data?.forEach((p) => p.type && set.add(p.type));
    const current = sp.get("tipo");
    if (current) set.add(current);
    return [...set].sort().map((t) => ({ value: t, label: t }));
  }, [all.data, sp]);

  const sourceOptions = useMemo(() => {
    const set = new Set<string>(Object.keys(settings.data?.sources_enabled ?? {}));
    all.data?.forEach((p) => set.add(p.source));
    const current = sp.get("fonte");
    if (current) set.add(current);
    return [...set].sort().map((s) => ({ value: s, label: sourceLabel(s) }));
  }, [all.data, settings.data, sp]);

  const activeFilters = ["priorita", "zona", "tipo", "stato", "fonte", "q", "scartati"].filter((k) => sp.get(k)).length;

  return (
    <>
      <PageHeader title="Annunci" subtitle="Filtrati e valutati ogni giorno. Decidi tu cosa candidare." actions={<AddFromUrl />} />

      <div role="search" className="card mb-5 p-4" aria-label="Filtri annunci">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="sm:col-span-2">
            <label htmlFor={searchId} className="label">
              Cerca
            </label>
            <div className="relative">
              <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" />
              <input
                id={searchId}
                type="search"
                className="field pl-9"
                placeholder="Titolo, azienda o città"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>
          <button
            type="button"
            className="btn-secondary md:hidden"
            aria-expanded={filtersOpen}
            aria-controls={filtersId}
            onClick={() => setFiltersOpen((o) => !o)}
          >
            <Icon name="chevronDown" size={14} className={filtersOpen ? "rotate-180" : ""} />
            {filtersOpen ? "Nascondi filtri" : "Mostra filtri e ordinamento"}
            {activeFilters > 0 && <span className="text-ink-muted">({activeFilters} attivi)</span>}
          </button>
          <div id={filtersId} className={filtersOpen ? "contents" : "hidden md:contents"}>
          <FilterSelect
            label="Priorità"
            param="priorita"
            sp={sp}
            update={update}
            allLabel="Tutte"
            options={[
              { value: "Alta", label: "Alta" },
              { value: "Media", label: "Media" },
              { value: "Bassa", label: "Bassa" },
            ]}
          />
          <FilterSelect
            label="Zona"
            param="zona"
            sp={sp}
            update={update}
            allLabel="Tutte"
            options={[
              { value: "Italia", label: "Italia" },
              { value: "Europa", label: "Europa" },
              { value: "Altro", label: "Altro" },
            ]}
          />
          <FilterSelect label="Tipo" param="tipo" sp={sp} update={update} options={typeOptions} />
          <FilterSelect label="Stato" param="stato" sp={sp} update={update} options={STATE_OPTIONS} />
          <FilterSelect label="Fonte" param="fonte" sp={sp} update={update} allLabel="Tutte" options={sourceOptions} />
          <div>
            <label htmlFor={sortId} className="label">
              Ordina per
            </label>
            <select id={sortId} className="field" value={query.sort} onChange={(e) => update("ordina", e.target.value === "priority" ? null : e.target.value)}>
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          </div>
        </div>
        <div className={`mt-3 flex-wrap items-center justify-between gap-3 ${filtersOpen ? "flex" : "hidden md:flex"}`}>
          <div className="flex items-center gap-2">
            <input
              id={rejectedId}
              type="checkbox"
              className="h-4 w-4 accent-[var(--color-accent)]"
              checked={sp.get("scartati") === "1"}
              onChange={(e) => update("scartati", e.target.checked ? "1" : null)}
            />
            <label htmlFor={rejectedId} className="text-sm">
              Mostra scartati dai filtri
            </label>
          </div>
          {activeFilters > 0 && (
            <button
              type="button"
              className="btn-ghost btn-sm"
              onClick={() => {
                setSearch("");
                setSp(new URLSearchParams(), { replace: true });
              }}
            >
              <Icon name="x" size={14} /> Azzera filtri
            </button>
          )}
        </div>
      </div>

      <p className="mb-3 text-sm text-ink-muted" aria-live="polite" aria-atomic="true">
        {isPending ? "" : data ? `${data.length === 1 ? "1 annuncio" : `${data.length} annunci`}${isFetching ? " · aggiornamento…" : ""}` : ""}
      </p>

      {isPending ? (
        <ListSkeleton rows={5} label="Caricamento annunci…" />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : data.length === 0 ? (
        <EmptyState title="Nessun annuncio con questi filtri" icon="search">
          {activeFilters > 0 ? "Prova ad allentare i filtri o ad azzerarli." : "Gli annunci compariranno dopo la prossima esecuzione di radar run."}
        </EmptyState>
      ) : (
        <>
        <h2 className="sr-only">Elenco annunci</h2>
        <ul className="space-y-3" aria-label="Elenco annunci">
          {data.map((p) => (
            <PostingRow key={p.id} posting={p} defaultOpen={p.id === openId} />
          ))}
        </ul>
        </>
      )}
    </>
  );
}
