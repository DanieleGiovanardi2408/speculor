import { useEffect, useId, useMemo, useState } from "react";
import { usePatchCompany } from "../api/hooks";
import { useCompanies } from "../api/hooks";
import type { Company } from "../api/types";
import { Badge } from "../components/Badge";
import { ExternalLink } from "../components/ExternalLink";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, ListSkeleton } from "../components/States";
import { useToast } from "../components/Toast";
import { Link } from "react-router-dom";

function CompanyPriority({ value }: { value: string | null }) {
  if (!value) return <span className="text-ink-muted">—</span>;
  const tone = value === "Alta" ? "danger" : value === "Media" ? "warning" : "neutral";
  const icon = value === "Alta" ? "arrowUp" : value === "Media" ? "minus" : "arrowDown";
  return (
    <Badge tone={tone} icon={icon}>
      {value}
    </Badge>
  );
}

function NotesField({ company }: { company: Company }) {
  const id = useId();
  const [v, setV] = useState(company.notes ?? "");
  const patch = usePatchCompany();
  const toast = useToast();
  useEffect(() => setV(company.notes ?? ""), [company.notes]);
  const commit = () => {
    if (v === (company.notes ?? "")) return;
    patch.mutate(
      { id: company.id, patch: { notes: v.trim() ? v : null } },
      { onSuccess: () => toast.success(`Note salvate per ${company.name}.`), onError: (e) => toast.error(`Note non salvate: ${e.message}`) },
    );
  };
  return (
    <>
      <label htmlFor={id} className="sr-only">
        Note su {company.name}
      </label>
      <textarea
        id={id}
        rows={1}
        className="field min-w-48 resize-y py-1.5"
        value={v}
        placeholder="Aggiungi una nota"
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && (e.ctrlKey || e.metaKey) && commit()}
      />
    </>
  );
}

function OpenPostings({ c }: { c: Company }) {
  if (c.open_postings === 0) return <span className="text-ink-muted">0</span>;
  return (
    <Link to={`/annunci?q=${encodeURIComponent(c.name)}`} className="link tabular-nums">
      {c.open_postings}
      <span className="sr-only"> annunci aperti di {c.name}</span>
    </Link>
  );
}

export function CompaniesPage() {
  const { data, isPending, isError, error, refetch } = useCompanies();
  const [q, setQ] = useState("");
  const searchId = useId();
  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data ?? [])
      .filter((c) => !needle || `${c.name} ${c.sector ?? ""} ${c.city ?? ""}`.toLowerCase().includes(needle))
      .sort((a, b) => a.name.localeCompare(b.name, "it"));
  }, [data, q]);

  return (
    <>
      <PageHeader title="Aziende" subtitle="Le aziende che Radar tiene d'occhio. Elenco gestito in config/companies.yaml." />
      <div className="mb-4 max-w-sm">
        <label htmlFor={searchId} className="label">
          Cerca azienda
        </label>
        <div className="relative">
          <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" />
          <input id={searchId} type="search" className="field pl-9" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Nome, settore o città" />
        </div>
      </div>
      {isPending ? (
        <ListSkeleton rows={5} label="Caricamento aziende…" />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : list.length === 0 ? (
        <EmptyState title={q ? "Nessuna azienda trovata" : "Nessuna azienda configurata"} icon="building" />
      ) : (
        <>
          {/* Desktop table */}
          <div className="card relative hidden overflow-x-auto md:block" role="region" aria-label="Tabella aziende" tabIndex={0}>
            <table className="w-full text-sm">
              <caption className="sr-only">Aziende monitorate</caption>
              <thead>
                <tr className="border-b border-line text-left text-xs text-ink-muted">
                  {["Azienda", "Settore", "Città", "Priorità", "ATS", "Annunci aperti", "Carriere", "Note"].map((h) => (
                    <th key={h} scope="col" className="whitespace-nowrap px-4 py-3 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {list.map((c) => (
                  <tr key={c.id} className="border-b border-line/70 align-top last:border-0">
                    <th scope="row" className="px-4 py-3 text-left font-medium">
                      {c.name}
                      {!c.active && (
                        <span className="mt-1 block">
                          <Badge tone="neutral" icon="lock">
                            Non attiva
                          </Badge>
                        </span>
                      )}
                      {c.program && <span className="mt-0.5 block text-xs font-normal text-ink-muted">{c.program}</span>}
                    </th>
                    <td className="px-4 py-3">{c.sector ?? "—"}</td>
                    <td className="px-4 py-3">{c.city ?? "—"}</td>
                    <td className="px-4 py-3">
                      <CompanyPriority value={c.priority} />
                    </td>
                    <td className="px-4 py-3">{c.ats ?? "—"}</td>
                    <td className="px-4 py-3 text-right">
                      <OpenPostings c={c} />
                    </td>
                    <td className="px-4 py-3">
                      {c.careers_url ? (
                        <ExternalLink href={c.careers_url}>
                          Apri<span className="sr-only"> pagina carriere di {c.name}</span>
                        </ExternalLink>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <NotesField company={c} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <ul className="space-y-3 md:hidden">
            {list.map((c) => (
              <li key={c.id} className="card p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-medium">{c.name}</p>
                    <p className="text-sm text-ink-muted">{[c.sector, c.city, c.ats].filter(Boolean).join(" · ")}</p>
                  </div>
                  <CompanyPriority value={c.priority} />
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                  <span>
                    Annunci aperti: <OpenPostings c={c} />
                  </span>
                  {c.careers_url && <ExternalLink href={c.careers_url}>Pagina carriere</ExternalLink>}
                  {!c.active && (
                    <Badge tone="neutral" icon="lock">
                      Non attiva
                    </Badge>
                  )}
                </div>
                <div className="mt-3">
                  <NotesField company={c} />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
