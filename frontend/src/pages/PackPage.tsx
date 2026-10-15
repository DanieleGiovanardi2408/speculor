import { useEffect, useId, useState } from "react";
import { Link, useBlocker, useParams } from "react-router-dom";
import { useGeneratePack, usePack, usePatchPack, usePosting } from "../api/hooks";
import type { FormAnswer, Pack, PackPatch, PostingSummary } from "../api/types";
import { FilterStateBadge, PriorityBadge, UserStatusBadge } from "../components/Badge";
import { CharCounter } from "../components/CharCounter";
import { ContactsSection } from "../components/ContactsSection";
import { CopyButton } from "../components/CopyButton";
import { UnsavedIndicator } from "../components/DraftCard";
import { ExternalLink } from "../components/ExternalLink";
import { Icon } from "../components/Icon";
import { MarkSentDialog, useCvVersions } from "../components/MarkSentDialog";
import { PageHeader, Section } from "../components/PageHeader";
import { EmptyState, ErrorState, ListSkeleton, Notice } from "../components/States";
import { useToast } from "../components/Toast";
import { formatDate, formatDateTime, wordCount } from "../lib/format";
import { sourceLabel } from "../lib/labels";

const MAX_WORDS = 250;

function PostingHeader({ p }: { p: PostingSummary }) {
  return (
    <div className="card mb-6 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <PriorityBadge priority={p.priority} />
        <FilterStateBadge state={p.filter_state} />
        <UserStatusBadge status={p.user_status} />
      </div>
      <p className="mt-2 text-lg font-semibold">{p.title}</p>
      <p className="text-sm text-ink-muted">
        {p.company_name}
        {p.location && ` · ${p.location}`} · {sourceLabel(p.source)}
        {p.deadline && ` · scade il ${formatDate(p.deadline)}`}
      </p>
      <p className="mt-3">
        <ExternalLink href={p.url}>Apri annuncio originale</ExternalLink>
      </p>
    </div>
  );
}

function PackEditor({ pack }: { pack: Pack }) {
  const ids = { cover: useId(), coverCount: useId(), cv: useId() };
  const [cover, setCover] = useState(pack.cover_letter);
  const [answers, setAnswers] = useState<FormAnswer[]>(pack.form_answers);
  const [cv, setCv] = useState(pack.cv_version);
  const [sentOpen, setSentOpen] = useState(false);
  const cvs = useCvVersions([pack.cv_version, cv]);
  const patch = usePatchPack(pack.posting_id);
  const toast = useToast();

  // Re-sync when the server version changes (e.g. after save).
  useEffect(() => {
    setCover(pack.cover_letter);
    setAnswers(pack.form_answers);
    setCv(pack.cv_version);
    // Only on a new server version: contact/draft cache updates must not wipe local edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pack.updated_at]);

  const changes: PackPatch = {};
  if (cover !== pack.cover_letter) changes.cover_letter = cover;
  if (JSON.stringify(answers) !== JSON.stringify(pack.form_answers)) changes.form_answers = answers;
  if (cv !== pack.cv_version) changes.cv_version = cv;
  const dirty = Object.keys(changes).length > 0;

  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state === "blocked") {
      if (window.confirm("Hai modifiche non salvate al pacchetto. Uscire comunque?")) blocker.proceed();
      else blocker.reset();
    }
  }, [blocker]);
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  function save() {
    patch.mutate(
      { id: pack.id, patch: changes },
      { onSuccess: () => toast.success("Pacchetto salvato."), onError: (e) => toast.error(`Salvataggio non riuscito: ${e.message}`) },
    );
  }

  const words = wordCount(cover);
  const allIssues = pack.style_issues;

  return (
    <>
      <div className="sticky top-[61px] z-20 mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface/95 p-3 shadow-sm backdrop-blur md:top-3">
        <UnsavedIndicator dirty={dirty} />
        {!dirty && <span className="text-xs text-ink-muted">Tutte le modifiche sono salvate.</span>}
        <button type="button" className="btn-primary" disabled={!dirty || patch.isPending} onClick={save}>
          {patch.isPending ? "Salvataggio…" : "Salva modifiche"}
        </button>
      </div>

      {allIssues.length > 0 && (
        <div className="mb-6">
          <Notice tone="warning" title="Avvisi di stile da rivedere">
            <ul className="list-disc pl-5">
              {allIssues.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          </Notice>
        </div>
      )}

      <Section title="Lettera di presentazione" actions={<CopyButton text={cover} srContext="lettera di presentazione" />}>
        <label htmlFor={ids.cover} className="sr-only">
          Testo della lettera di presentazione
        </label>
        <textarea
          id={ids.cover}
          className={`field min-h-72 font-[inherit] leading-relaxed ${words > MAX_WORDS ? "border-danger" : ""}`}
          rows={Math.min(32, Math.max(12, cover.split("\n").reduce((n, l) => n + Math.max(1, Math.ceil(l.length / 95)), 0) + 1))}
          value={cover}
          onChange={(e) => setCover(e.target.value)}
          aria-describedby={ids.coverCount}
          aria-invalid={words > MAX_WORDS}
          lang={pack.language}
        />
        <p className="mt-1.5 flex flex-wrap justify-between gap-2">
          <CharCounter id={ids.coverCount} count={words} max={MAX_WORDS} unit="parole" />
          <span className="text-xs text-ink-muted">
            Lingua: {pack.language.toUpperCase()} · generata con {pack.model} · aggiornata {formatDateTime(pack.updated_at)}
          </span>
        </p>
      </Section>

      <Section title="Risposte ai moduli">
        {answers.length === 0 ? (
          <p className="text-sm text-ink-muted">Nessuna domanda rilevata nel modulo di candidatura.</p>
        ) : (
          <ol className="space-y-4">
            {answers.map((a, i) => (
              <li key={i} className="card p-4">
                <div className="mb-2 flex items-start justify-between gap-3">
                  <label htmlFor={`${ids.cover}-qa-${i}`} className="text-sm font-medium">
                    {a.question}
                  </label>
                  <CopyButton text={a.answer} srContext={`risposta alla domanda ${i + 1}`} />
                </div>
                <textarea
                  id={`${ids.cover}-qa-${i}`}
                  className="field leading-relaxed"
                  rows={3}
                  value={a.answer}
                  onChange={(e) => setAnswers((xs) => xs.map((x, j) => (j === i ? { ...x, answer: e.target.value } : x)))}
                  lang={pack.language}
                />
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section title="CV e invio">
        <div className="card flex flex-col gap-4 p-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="sm:w-72">
            <label htmlFor={ids.cv} className="label">
              Versione CV
            </label>
            <select id={ids.cv} className="field" value={cv} onChange={(e) => setCv(e.target.value)}>
              {cvs.map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </div>
          <ExternalLink href={pack.apply_link} className="btn-primary">
            Vai alla pagina di candidatura
          </ExternalLink>
        </div>
        <p className="mt-2 text-xs text-ink-muted">Radar non compila né invia moduli: copia i testi e candidati tu dalla pagina dell'azienda.</p>
      </Section>

      <Section title="Contatti e messaggi">
        <ContactsSection postingId={pack.posting_id} contacts={pack.contacts} drafts={pack.drafts} />
      </Section>

      <section aria-labelledby="send-title" className="card mt-10 p-5">
        <h2 id="send-title" className="text-base font-semibold">
          Hai inviato la candidatura?
        </h2>
        <p className="mt-1 text-sm text-ink-muted">Registrala per tenerla nella pipeline e ricevere il promemoria di follow-up dopo 14 giorni.</p>
        <button type="button" className="btn-primary mt-4" onClick={() => setSentOpen(true)} disabled={pack.posting.user_status === "sent"}>
          <Icon name="check" size={14} />
          {pack.posting.user_status === "sent" ? "Già segnata come inviata" : "Segna come inviata"}
        </button>
      </section>
      <MarkSentDialog open={sentOpen} onClose={() => setSentOpen(false)} postingId={pack.posting_id} postingTitle={pack.posting.title} defaultCv={cv} />
    </>
  );
}

export function PackPage() {
  const id = Number(useParams().id);
  const posting = usePosting(id);
  const pack = usePack(id);
  const generate = useGeneratePack();
  const toast = useToast();

  const back = (
    <Link to={`/annunci?open=${id}`} className="link mb-3 inline-flex items-center gap-1 text-sm">
      <Icon name="chevronRight" size={14} className="rotate-180" /> Torna agli annunci
    </Link>
  );

  return (
    <>
      {back}
      <PageHeader title="Pacchetto candidatura" subtitle="Rivedi e personalizza prima di candidarti." />
      {pack.isPending || posting.isPending ? (
        <ListSkeleton rows={3} label="Caricamento pacchetto…" />
      ) : pack.isError ? (
        <ErrorState error={pack.error} onRetry={() => pack.refetch()} />
      ) : posting.isError && !pack.data ? (
        <ErrorState error={posting.error} onRetry={() => posting.refetch()} />
      ) : (
        <>
          <PostingHeader p={pack.data?.posting ?? posting.data!} />
          {pack.data ? (
            <PackEditor pack={pack.data} />
          ) : (
            <EmptyState
              title="Nessun pacchetto per questo annuncio"
              icon="file"
              action={
                <button
                  type="button"
                  className="btn-primary"
                  disabled={generate.isPending}
                  onClick={() =>
                    generate.mutate(id, {
                      onSuccess: () => toast.success("Pacchetto generato."),
                      onError: (e) => toast.error(`Generazione non riuscita: ${e.message}`),
                    })
                  }
                >
                  <Icon name="sparkles" size={14} /> {generate.isPending ? "Generazione in corso…" : "Genera pacchetto"}
                </button>
              }
            >
              Genera una bozza di lettera e risposte ai moduli: potrai modificarle prima di usarle.
            </EmptyState>
          )}
        </>
      )}
    </>
  );
}
