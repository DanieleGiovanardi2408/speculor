import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAddPosting, usePostings, useSettings } from "../api/hooks";
import type { PostingSort, PostingSummary, PostingsQuery } from "../api/types";
import { Dialog } from "../components/Dialog";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { usePostingActions } from "../components/PostingActions";
import { PostingRow } from "../components/PostingRow";
import { EmptyState, ErrorState, ListSkeleton } from "../components/States";
import { useToast } from "../components/Toast";
import { sourceLabel } from "../lib/labels";
import { ruleLabel } from "../lib/plain";
import { useShortcuts } from "../lib/shortcuts";

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

export type View = "stage" | "laurea" | "scartati";

export function viewOf(sp: URLSearchParams): View {
  const v = sp.get("vista");
  if (v === "laurea" || v === "scartati") return v;
  return sp.get("scartati") === "1" ? "scartati" : "stage";
}

const VIEW_INFO: Record<View, { label: string; line: string }> = {
  stage: { label: "Stage", line: "Stage e tirocini, filtrati e valutati ogni giorno. Decidi tu cosa candidare." },
  laurea: {
    label: "Dopo la laurea",
    line: "Graduate program e primi ruoli junior che partono a laurea conseguita: da tenere d'occhio per dopo, senza fretta.",
  },
  scartati: {
    label: "Scartati",
    line: "Annunci esclusi dai filtri automatici. Vedi quale regola li ha scartati e ripristinali con un clic se è un errore.",
  },
};

/** URL search params <-> API query. Italian param names keep shared links readable. */
function toQuery(sp: URLSearchParams): PostingsQuery {
  const state = STATE_OPTIONS.find((s) => s.value === sp.get("stato"));
  const sort = sp.get("ordina") as PostingSort | null;
  const view = viewOf(sp);
  return {
    priority: sp.get("priorita") ?? undefined,
    zone: sp.get("zona") ?? undefined,
    type: sp.get("tipo") ?? undefined,
    source: sp.get("fonte") ?? undefined,
    q: sp.get("q") ?? undefined,
    sort: sort && SORT_OPTIONS.some((o) => o.value === sort) ? sort : "priority",
    track: view === "laurea" ? "post_graduation" : view === "scartati" ? "all" : "internship",
    only_rejected: view === "scartati" ? true : undefined,
    ...(state?.query ?? {}),
  };
}

/** Fallback when the server does not know `track` yet: guess from the posting type. */
const looksPostGraduation = (p: PostingSummary) => /graduate|full.?time|lavoro|junior|entry|permanent/i.test(p.type ?? "");

export function applyTrackFallback(items: PostingSummary[], view: View): PostingSummary[] {
  if (view === "scartati" || items.length === 0 || items.some((p) => "track" in p)) return items;
  return items.filter((p) => (view === "laurea" ? looksPostGraduation(p) : !looksPostGraduation(p)));
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
        toast.success(`Annuncio aggiunto: ${p.title}.`);
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

function RuleChips({ items, value, onChange }: { items: PostingSummary[]; value: string | null; onChange: (v: string | null) => void }) {
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    items.forEach((p) => new Set(p.filter_hits.filter((h) => h.action === "reject").map((h) => h.rule_id)).forEach((r) => m.set(r, (m.get(r) ?? 0) + 1)));
    return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [items]);
  if (counts.length === 0) return null;
  const chip = (active: boolean) =>
    `inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3 text-sm font-medium sm:min-h-9 ${active ? "border-accent bg-accent-soft text-accent-ink" : "border-line-strong bg-surface text-ink hover:bg-subtle"}`;
  return (
    <div role="group" aria-label="Filtra per regola che ha scartato l'annuncio" className="mb-4 flex flex-wrap gap-2">
      <button type="button" aria-pressed={!value} className={chip(!value)} onClick={() => onChange(null)}>
        {!value && <Icon name="check" size={14} />}
        Tutte le regole <span className="tabular-nums text-ink-muted">{items.length}</span>
      </button>
      {counts.map(([rule, n]) => (
        <button key={rule} type="button" aria-pressed={value === rule} className={chip(value === rule)} onClick={() => onChange(value === rule ? null : rule)}>
          {value === rule && <Icon name="check" size={14} />}
          {ruleLabel(rule)} <span className="tabular-nums text-ink-muted">{n}</span>
        </button>
      ))}
    </div>
  );
}

function ViewSwitch({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  const views: View[] = ["stage", "laurea", "scartati"];
  return (
    <nav aria-label="Vista annunci" className="mb-2">
      <ul className="inline-flex w-full rounded-xl border border-line bg-subtle p-1 sm:w-auto">
        {views.map((v) => (
          <li key={v} className="flex-1 sm:flex-none">
            <button
              type="button"
              aria-current={view === v ? "page" : undefined}
              onClick={() => onChange(v)}
              className={`min-h-11 w-full whitespace-nowrap rounded-lg px-3 text-sm font-medium sm:min-h-9 sm:px-4 ${
                view === v ? "bg-surface font-semibold text-ink shadow-sm ring-1 ring-line" : "text-ink-muted hover:text-ink"
              }`}
            >
              {VIEW_INFO[v].label}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function PostingsPage() {
  const [sp, setSp] = useSearchParams();
  const view = viewOf(sp);
  const query = useMemo(() => toQuery(sp), [sp]);
  const { data: raw, isPending, isError, error, refetch, isFetching } = usePostings(query);
  // Unfiltered list to derive type/source options.
  const all = usePostings({ include_rejected: true, track: "all" });
  const settings = useSettings();
  const actions = usePostingActions();
  const navigate = useNavigate();
  const openId = Number(sp.get("open"));
  const rule = view === "scartati" ? sp.get("regola") : null;
  const searchId = useId();
  const sortId = useId();
  const filtersId = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  // Render in pages: ~1000 expandable cards at once makes filtering sluggish.
  const PAGE = 100;
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => setLimit(PAGE), [sp]);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set(Number.isFinite(openId) && openId > 0 ? [openId] : []));
  const [cursor, setCursor] = useState(-1);
  const [announce, setAnnounce] = useState("");

  const viewItems = useMemo(() => (raw ? applyTrackFallback(raw, view) : undefined), [raw, view]);
  const data = useMemo(
    () => (viewItems && rule ? viewItems.filter((p) => p.filter_hits.some((h) => h.action === "reject" && h.rule_id === rule)) : viewItems),
    [viewItems, rule],
  );

  const [search, setSearch] = useState(sp.get("q") ?? "");
  useEffect(() => {
    const t = window.setTimeout(() => {
      if ((sp.get("q") ?? "") !== search) update("q", search.trim() || null);
    }, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  useEffect(() => {
    if (Number.isFinite(openId) && openId > 0) setExpanded((s) => (s.has(openId) ? s : new Set([...s, openId])));
  }, [openId]);
  useEffect(() => setCursor(-1), [query, rule]);

  function update(key: string, value: string | null) {
    setSp(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value == null || value === "") next.delete(key);
        else next.set(key, value);
        next.delete("open");
        if (key === "vista") {
          next.delete("scartati");
          next.delete("regola");
        }
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

  const activeFilters = ["priorita", "zona", "tipo", "stato", "fonte", "q"].filter((k) => sp.get(k)).length;
  const shown = data ? data.slice(0, Math.max(limit, data.findIndex((p) => p.id === openId) + 1)) : [];

  const toggle = useCallback((id: number) => setExpanded((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  }), []);

  function focusRow(index: number) {
    if (!data || data.length === 0) return;
    if (index < 0) {
      setAnnounce("Sei al primo annuncio.");
      index = 0;
    } else if (index >= data.length) {
      setAnnounce("Fine dell'elenco.");
      index = data.length - 1;
    } else setAnnounce("");
    if (index >= limit) setLimit(Math.ceil((index + 1) / PAGE) * PAGE);
    setCursor(index);
    const id = data[index]!.id;
    window.requestAnimationFrame(() => {
      const btn = listRef.current?.querySelector<HTMLButtonElement>(`[data-row-toggle="${id}"]`);
      btn?.focus({ preventScroll: true });
      btn?.scrollIntoView({ block: "nearest" });
    });
  }

  const current = data && cursor >= 0 ? data[cursor] : undefined;
  const onRowButton = () => document.activeElement instanceof HTMLElement && document.activeElement.hasAttribute("data-row-toggle");
  useShortcuts({
    j: () => focusRow(cursor + 1),
    k: () => focusRow(cursor - 1),
    ArrowDown: () => (onRowButton() ? focusRow(cursor + 1) : false),
    ArrowUp: () => (onRowButton() ? focusRow(cursor - 1) : false),
    o: () => (current ? toggle(current.id) : false),
    Enter: () => (current ? toggle(current.id) : false),
    p: () => (current ? navigate(`/annunci/${current.id}/pacchetto`) : false),
    s: () => (current && current.user_status !== "sent" ? actions.markSent(current) : false),
    x: () => (current && current.user_status === "todo" ? actions.skip(current) : false),
    r: () => (current && current.filter_state === "rejected" ? actions.restore(current) : false),
    "/": () => searchRef.current?.focus(),
  });

  return (
    <>
      <PageHeader title="Annunci" subtitle={VIEW_INFO[view].line} actions={<AddFromUrl />} />
      <ViewSwitch view={view} onChange={(v) => update("vista", v === "stage" ? null : v)} />
      <p className="sr-only" aria-live="polite">
        {`Vista ${VIEW_INFO[view].label}`}
      </p>

      <div role="search" className="card mb-5 mt-4 p-4" aria-label="Filtri annunci">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="sm:col-span-2">
            <label htmlFor={searchId} className="label">
              Cerca <span className="font-normal text-ink-muted">(tasto /)</span>
            </label>
            <div className="relative">
              <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" />
              <input
                ref={searchRef}
                id={searchId}
                type="search"
                className="field pl-9"
                placeholder="Titolo, azienda o città"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") e.currentTarget.blur();
                }}
                aria-keyshortcuts="/"
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
        {activeFilters > 0 && (
          <div className={`mt-3 justify-end ${filtersOpen ? "flex" : "hidden md:flex"}`}>
            <button
              type="button"
              className="btn-ghost btn-sm"
              onClick={() => {
                setSearch("");
                setSp(view === "stage" ? new URLSearchParams() : new URLSearchParams({ vista: view }), { replace: true });
              }}
            >
              <Icon name="x" size={14} /> Azzera filtri
            </button>
          </div>
        )}
      </div>

      {view === "scartati" && viewItems && <RuleChips items={viewItems} value={rule} onChange={(v) => update("regola", v)} />}

      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-ink-muted" aria-live="polite" aria-atomic="true">
          {isPending ? "" : data ? `${data.length === 1 ? "1 annuncio" : `${data.length} annunci`}${isFetching ? " · aggiornamento…" : ""}` : ""}
        </p>
        <p className="hidden text-xs text-ink-muted md:block">
          Tastiera: <kbd className="rounded border border-line-strong bg-surface px-1">j</kbd> <kbd className="rounded border border-line-strong bg-surface px-1">k</kbd> per
          muoverti, <kbd className="rounded border border-line-strong bg-surface px-1">?</kbd> per tutte le scorciatoie
        </p>
      </div>
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>

      {isPending ? (
        <ListSkeleton rows={5} label="Caricamento annunci…" />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : !data || data.length === 0 ? (
        <EmptyState title={emptyTitle(view, activeFilters > 0 || !!rule)} icon={view === "scartati" ? "check" : "search"}>
          {emptyText(view, activeFilters > 0 || !!rule)}
        </EmptyState>
      ) : (
        <>
        <h2 className="sr-only">Elenco annunci</h2>
        <ul
          ref={listRef}
          className="space-y-3"
          aria-label={`Elenco annunci, vista ${VIEW_INFO[view].label}`}
          onFocusCapture={(e) => {
            const id = Number((e.target as HTMLElement).closest("[data-posting-id]")?.getAttribute("data-posting-id"));
            if (id) {
              const i = data.findIndex((p) => p.id === id);
              if (i >= 0 && i !== cursor) setCursor(i);
            }
          }}
        >
          {shown.map((p, i) => (
            <PostingRow
              key={p.id}
              posting={p}
              defaultOpen={p.id === openId}
              open={expanded.has(p.id)}
              onToggle={() => toggle(p.id)}
              active={i === cursor}
              position={{ index: i, total: data.length }}
            />
          ))}
        </ul>
        {data.length > shown.length && (
          <div className="mt-4 flex justify-center">
            <button type="button" className="btn-secondary" onClick={() => setLimit((l) => l + PAGE)}>
              Mostra altri {Math.min(PAGE, data.length - shown.length)} (ne restano {data.length - shown.length})
            </button>
          </div>
        )}
        </>
      )}
    </>
  );
}

function emptyTitle(view: View, filtered: boolean): string {
  if (filtered) return "Nessun annuncio con questi filtri";
  if (view === "scartati") return "Nessun annuncio scartato";
  if (view === "laurea") return "Nessun annuncio dopo la laurea, per ora";
  return "Nessun annuncio, per ora";
}

function emptyText(view: View, filtered: boolean): string {
  if (filtered) return "Prova ad allentare i filtri o ad azzerarli.";
  if (view === "scartati") return "I filtri non hanno escluso nulla: tutto quello che è arrivato è tra gli annunci attivi.";
  if (view === "laurea") return "Qui compaiono graduate program e ruoli junior. Il prossimo giro è domattina alle 7.";
  return "Gli annunci nuovi arrivano con il prossimo giro, domattina alle 7. Puoi anche aggiungerne uno da un link.";
}
