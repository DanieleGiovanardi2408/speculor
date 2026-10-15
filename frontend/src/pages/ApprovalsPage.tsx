import { Link, useSearchParams } from "react-router-dom";
import { useDrafts } from "../api/hooks";
import type { Draft, DraftStatus } from "../api/types";
import { DraftCard } from "../components/DraftCard";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, ListSkeleton, Notice } from "../components/States";
import { Tabs } from "../components/Tabs";

type TabKey = "draft" | "approved" | "sent";

function groupByPosting(drafts: Draft[]) {
  const groups = new Map<string, { title: string; company: string | null; postingId: number | null; items: Draft[] }>();
  for (const d of drafts) {
    const key = String(d.posting_id ?? "none");
    const g = groups.get(key) ?? {
      title: d.posting_title ?? "Senza annuncio collegato",
      company: d.company_name,
      postingId: d.posting_id,
      items: [],
    };
    g.items.push(d);
    groups.set(key, g);
  }
  return [...groups.values()];
}

function Count({ status }: { status: DraftStatus }) {
  const { data } = useDrafts(status);
  if (!data) return null;
  return <span className="rounded-full bg-subtle px-1.5 text-xs tabular-nums leading-5 text-ink-muted">{data.length}</span>;
}

function DraftList({ status }: { status: TabKey }) {
  const { data, isPending, isError, error, refetch } = useDrafts(status);
  if (isPending) return <ListSkeleton rows={3} label="Caricamento messaggi…" />;
  if (isError) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (data.length === 0)
    return (
      <EmptyState title={status === "draft" ? "Nessuna bozza da approvare" : status === "approved" ? "Nessun messaggio approvato in attesa" : "Nessun messaggio inviato"} icon="inbox">
        {status === "draft" && "Le nuove bozze compaiono qui quando generi messaggi per i contatti di un pacchetto."}
      </EmptyState>
    );
  return (
    <div className="space-y-8">
      {groupByPosting(data).map((g) => (
        <section key={g.postingId ?? "none"} aria-label={`${g.title}${g.company ? `, ${g.company}` : ""}`}>
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-base font-semibold">
              {g.title}
              {g.company && <span className="font-normal text-ink-muted"> · {g.company}</span>}
            </h2>
            {g.postingId != null && (
              <Link to={`/annunci/${g.postingId}/pacchetto`} className="link text-sm">
                Apri pacchetto
              </Link>
            )}
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {g.items.map((d) => (
              <DraftCard key={d.id} draft={d} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export function ApprovalsPage() {
  const [sp, setSp] = useSearchParams();
  const raw = sp.get("tab");
  const tab: TabKey = raw === "approved" || raw === "sent" ? raw : "draft";

  return (
    <>
      <PageHeader title="Approvazioni" subtitle="Rivedi, modifica e approva i messaggi. Niente parte senza il tuo via libera." />
      <Tabs<TabKey>
        label="Stato dei messaggi"
        value={tab}
        onChange={(k) => setSp(k === "draft" ? {} : { tab: k }, { replace: true })}
        tabs={[
          { key: "draft", label: <>Da approvare <Count status="draft" /></> },
          { key: "approved", label: <>Approvate <Count status="approved" /></> },
          { key: "sent", label: <>Inviate <Count status="sent" /></> },
        ]}
      >
        {tab === "approved" && (
          <div className="mb-5">
            <Notice title="In attesa di invio dal tuo computer" icon="lock">
              I messaggi approvati vengono inviati solo dal tuo computer con <code className="font-mono font-semibold">radar send</code>, uno per
              uno, dopo la tua conferma. Se preferisci, copia il testo, invialo a mano e usa “Segna come inviata”.
            </Notice>
          </div>
        )}
        <DraftList status={tab} />
      </Tabs>
    </>
  );
}
