import { useMemo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { usePostings, useToday } from "../api/hooks";
import { Badge, PriorityBadge, type Tone } from "../components/Badge";
import { ConnectorPauseBanner } from "../components/ConnectorPauseBanner";
import { GoalEditor, WeeklyProgress } from "../components/GoalEditor";
import { Icon, type IconName } from "../components/Icon";
import { JobteaserPassCard } from "../components/JobteaserPassCard";
import { LastRunCard } from "../components/LastRunCard";
import { PageHeader } from "../components/PageHeader";
import { usePostingActions } from "../components/PostingActions";
import { PostingMiniList } from "../components/PostingMiniList";
import { EmptyState, ErrorState, Skeleton } from "../components/States";
import { ACTION_GROUP_LABEL, buildTodayActions, type ActionItem, type ActionKind } from "../lib/actions";

const rawToday = new Intl.DateTimeFormat("it-IT", { weekday: "long", day: "numeric", month: "long" }).format(new Date());
const todayLabel = rawToday.charAt(0).toUpperCase() + rawToday.slice(1);

const KIND_META: Record<ActionKind, { icon: IconName; tone: Tone }> = {
  deadline: { icon: "calendar", tone: "danger" },
  new_alta: { icon: "sparkles", tone: "accent" },
  pack: { icon: "file", tone: "accent" },
  followup: { icon: "clock", tone: "warning" },
  drafts: { icon: "inbox", tone: "info" },
  post_graduation: { icon: "briefcase", tone: "neutral" },
};

function Card({ title, icon, children, id }: { title: string; icon: IconName; children: ReactNode; id: string }) {
  return (
    <section aria-labelledby={id} className="card flex flex-col p-5">
      <h2 id={id} className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <Icon name={icon} className="text-ink-muted" />
        {title}
      </h2>
      <div className="flex-1">{children}</div>
    </section>
  );
}

function ActionRow({ item }: { item: ActionItem }) {
  const actions = usePostingActions();
  const meta = KIND_META[item.kind];
  const p = item.posting;
  return (
    <li className="flex flex-col gap-3 py-3.5 first:pt-0 last:pb-0 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="mb-1 flex flex-wrap items-center gap-1.5">
          <Badge tone={meta.tone} icon={meta.icon}>
            {ACTION_GROUP_LABEL[item.kind]}
          </Badge>
          {item.priority !== undefined && <PriorityBadge priority={item.priority} />}
        </p>
        <p className="font-medium leading-snug">
          <Link to={item.to} className="hover:underline">
            {item.title}
          </Link>
        </p>
        <p className="text-sm text-ink-muted">{item.subtitle}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        <Link to={item.to} className="btn-primary btn-sm">
          {item.cta}
          <span className="sr-only">: {item.title}</span>
        </Link>
        {item.secondary && (
          <Link to={item.secondary.to} className="btn-secondary btn-sm">
            {item.secondary.label}
            <span className="sr-only">: {item.title}</span>
          </Link>
        )}
        {p && (
          <>
            <button type="button" className="btn-ghost btn-sm" onClick={() => actions.markSent(p)}>
              <Icon name="check" size={14} /> Segna inviata<span className="sr-only">: {p.title}</span>
            </button>
            <button type="button" className="btn-ghost btn-sm" onClick={() => actions.skip(p)}>
              <Icon name="skip" size={14} /> Salta<span className="sr-only"> {p.title}</span>
            </button>
          </>
        )}
      </div>
    </li>
  );
}

export function TodayPage() {
  const { data, isPending, isError, error, refetch } = useToday();
  const todo = usePostings({ user_status: "todo", track: "all" });
  const ready = useMemo(() => todo.data?.filter((p) => p.has_pack) ?? [], [todo.data]);
  const items = useMemo(() => (data ? buildTodayActions(data, ready) : []), [data, ready]);

  return (
    <>
      <PageHeader title="Oggi" subtitle={todayLabel} />
      <div className="mb-4 empty:hidden">
        <ConnectorPauseBanner />
      </div>
      <div className="mb-4 empty:hidden">
        <JobteaserPassCard />
      </div>
      {isPending ? (
        <div className="space-y-4">
          <p role="status" className="sr-only">
            Caricamento del riepilogo…
          </p>
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="card space-y-3 p-5">
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-8 w-1/2" />
              <Skeleton className="h-3 w-full" />
            </div>
          ))}
        </div>
      ) : isError ? (
        <ErrorState error={error} onRetry={() => refetch()} title="Non riesco a preparare il riepilogo di oggi" />
      ) : (
        <div className="space-y-4">
          <section aria-labelledby="goal-title" className="card border-accent/30 p-5 sm:p-6">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <h2 id="goal-title" className="eyebrow">
                Obiettivo della settimana
              </h2>
              <GoalEditor goal={data.weekly_goal} />
            </div>
            <WeeklyProgress done={data.applications_this_week} goal={data.weekly_goal} />
          </section>

          <section aria-labelledby="todo-title" className="card p-5 sm:p-6">
            <h2 id="todo-title" className="mb-1 flex items-center gap-2 text-base font-semibold">
              <Icon name="check" className="text-accent" /> Cosa fare adesso
              {items.length > 0 && (
                <span className="ml-auto rounded-full bg-subtle px-2 text-xs font-semibold tabular-nums leading-5 text-ink-muted">
                  {items.length}
                  <span className="sr-only"> cose da fare</span>
                </span>
              )}
            </h2>
            <p className="mb-4 text-sm text-ink-muted">In ordine di urgenza: scadenze entro 7 giorni, nuovi annunci Alta, pacchetti pronti, follow-up, bozze.</p>
            {todo.isError && (
              <p className="mb-3 text-sm text-ink-muted">I pacchetti pronti non sono disponibili adesso: il resto dell'elenco è aggiornato.</p>
            )}
            {items.length === 0 ? (
              <EmptyState title="Niente da fare adesso." icon="check">
                Il prossimo giro è domattina alle 7.
              </EmptyState>
            ) : (
              <ol className="divide-y divide-line" aria-label="Cose da fare, in ordine di urgenza">
                {items.map((it) => (
                  <ActionRow key={it.key} item={it} />
                ))}
              </ol>
            )}
          </section>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card title="Ultima esecuzione" icon="clock" id="card-last-run">
              <LastRunCard run={data.last_run} closed={data.closed_since_yesterday} />
            </Card>
            <Card title="Tutti i nuovi da ieri" icon="sparkles" id="card-new">
              <PostingMiniList items={data.new_since_yesterday} empty="Nessun nuovo annuncio dall'ultima esecuzione." />
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
