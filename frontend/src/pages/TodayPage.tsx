import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useToday } from "../api/hooks";
import { Badge } from "../components/Badge";
import { GoalEditor, WeeklyProgress } from "../components/GoalEditor";
import { Icon, type IconName } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { PostingMiniList } from "../components/PostingMiniList";
import { RunSummaryView } from "../components/RunSummaryView";
import { ErrorState, Skeleton } from "../components/States";
import { daysUntil, formatDate } from "../lib/format";

const rawToday = new Intl.DateTimeFormat("it-IT", { weekday: "long", day: "numeric", month: "long" }).format(new Date());
const todayLabel = rawToday.charAt(0).toUpperCase() + rawToday.slice(1);

function Card({ title, count, icon, children, footer, wide = false }: { title: string; count?: number; icon: IconName; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const id = `card-${title.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  return (
    <section aria-labelledby={id} className={`card flex flex-col p-5 ${wide ? "md:col-span-2" : ""}`}>
      <h2 id={id} className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <Icon name={icon} className="text-ink-muted" />
        {title}
        {count != null && (
          <span className="ml-auto rounded-full bg-subtle px-2 text-xs font-semibold tabular-nums leading-5 text-ink-muted">{count}</span>
        )}
      </h2>
      <div className="flex-1">{children}</div>
      {footer && <div className="mt-4 border-t border-line pt-3 text-sm">{footer}</div>}
    </section>
  );
}

export function TodayPage() {
  const { data, isPending, isError, error, refetch } = useToday();

  return (
    <>
      <PageHeader title="Oggi" subtitle={todayLabel} />
      {isPending ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <p role="status" className="sr-only">
            Caricamento del riepilogo…
          </p>
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="card space-y-3 p-5">
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-8 w-1/2" />
              <Skeleton className="h-3 w-full" />
            </div>
          ))}
        </div>
      ) : isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : (
        <div className="space-y-4">
          <section aria-labelledby="goal-title" className="card p-5 sm:p-6">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <h2 id="goal-title" className="eyebrow">
                Obiettivo della settimana
              </h2>
              <GoalEditor goal={data.weekly_goal} />
            </div>
            <WeeklyProgress done={data.applications_this_week} goal={data.weekly_goal} />
          </section>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card
              title="Bozze da approvare"
              icon="inbox"
              count={data.pending_approvals}
              footer={
                <Link to="/approvazioni" className="link inline-flex items-center gap-1">
                  Vai alle approvazioni <Icon name="chevronRight" size={14} />
                </Link>
              }
            >
              <p className="text-sm text-ink-muted">
                {data.pending_approvals === 0
                  ? "Nessun messaggio in attesa: sei in pari."
                  : `${data.pending_approvals === 1 ? "C'è 1 messaggio" : `Ci sono ${data.pending_approvals} messaggi`} da rivedere prima dell'invio.`}
              </p>
            </Card>

            <Card title="Follow-up da fare" icon="clock" count={data.followups_due.length}>
              {data.followups_due.length === 0 ? (
                <p className="text-sm text-ink-muted">Nessuna candidatura senza risposta da più di 14 giorni.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {data.followups_due.map((a) => (
                    <li key={a.id} className="flex items-start justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{a.role}</p>
                        <p className="truncate text-xs text-ink-muted">
                          {a.company} · inviata il {formatDate(a.date_sent)}
                        </p>
                      </div>
                      <Badge tone="warning" icon="clock">
                        {-daysUntil(a.date_sent)} giorni
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
              {data.followups_due.length > 0 && (
                <Link to="/candidature" className="link mt-3 inline-block text-sm">
                  Apri le candidature
                </Link>
              )}
            </Card>

            <Card title="Scadenze in arrivo" icon="calendar" count={data.upcoming_deadlines.length}>
              <PostingMiniList items={data.upcoming_deadlines} showDeadline empty="Nessuna scadenza nei prossimi giorni." />
            </Card>

            <Card title="Nuovi da ieri" icon="sparkles" count={data.new_since_yesterday.length}>
              <PostingMiniList items={data.new_since_yesterday} empty="Nessun nuovo annuncio dall'ultima esecuzione." />
            </Card>

            <Card title="Chiusi da ieri" icon="lock" count={data.closed_since_yesterday.length}>
              <PostingMiniList items={data.closed_since_yesterday} empty="Nessun annuncio chiuso." />
            </Card>

            <Card title="Ultima esecuzione" icon="clock" wide>
              {data.last_run ? (
                <RunSummaryView run={data.last_run} />
              ) : (
                <p className="text-sm text-ink-muted">
                  Nessuna esecuzione registrata. Avvia <code className="font-mono text-ink">radar run</code> dal tuo computer.
                </p>
              )}
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
