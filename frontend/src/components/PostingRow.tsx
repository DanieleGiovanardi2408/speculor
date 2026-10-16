import { useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useGeneratePack, usePatchPosting, usePosting } from "../api/hooks";
import type { PostingSummary } from "../api/types";
import { daysUntil, formatDate, formatShortDate, relativeDays } from "../lib/format";
import { ruleLabel } from "../lib/plain";
import { noteBody, skipReasonOf } from "../lib/skip";
import { Badge, FilterStateBadge, PostingStatusBadge, PriorityBadge, UserStatusBadge } from "./Badge";
import { ExternalLink } from "./ExternalLink";
import { Icon } from "./Icon";
import { EvidenceSnippet } from "./Highlight";
import { usePostingActions } from "./PostingActions";
import { SourceChips, SourcesLine } from "./PostingSources";
import { useToast } from "./Toast";

function Deadline({ value }: { value: string | null }) {
  if (!value) return <span>Nessuna scadenza</span>;
  const d = daysUntil(value);
  const urgent = d >= 0 && d <= 3;
  return (
    <span className={urgent ? "font-medium text-danger-ink" : undefined}>
      {urgent && <Icon name="clock" size={12} className="mr-0.5 inline align-[-1px]" />}
      Scade <time dateTime={value}>{formatShortDate(value)}</time>
      {d >= 0 && d <= 14 && <> ({relativeDays(value)})</>}
      {d < 0 && <> (scaduto)</>}
    </span>
  );
}

function NoteField({ posting }: { posting: PostingSummary }) {
  const id = useId();
  // The "[Saltato: …]" prefix is shown as a badge; the backend keeps it when the note changes.
  const body = noteBody(posting.note);
  const [value, setValue] = useState(body);
  const [saved, setSaved] = useState<"idle" | "saving" | "saved">("idle");
  const patch = usePatchPosting();
  const toast = useToast();
  const lastSaved = useRef(body);

  useEffect(() => {
    setValue(body);
    lastSaved.current = body;
  }, [body]);

  function save() {
    if (value === lastSaved.current) return;
    setSaved("saving");
    const note = value.trim() ? value : null;
    patch.mutate(
      { id: posting.id, patch: { note } },
      {
        onSuccess: () => {
          lastSaved.current = value;
          setSaved("saved");
        },
        onError: (e) => {
          setSaved("idle");
          toast.error(`Nota non salvata. ${e.message}`);
        },
      },
    );
  }

  return (
    <div>
      <label htmlFor={id} className="label">
        Nota personale
      </label>
      <textarea
        id={id}
        className="field min-h-20"
        rows={2}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setSaved("idle");
        }}
        onBlur={save}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            save();
          }
        }}
        aria-describedby={`${id}-hint`}
        placeholder="Solo per te: contatti, dubbi, promemoria…"
      />
      <p id={`${id}-hint`} className="mt-1 flex justify-between text-xs text-ink-muted">
        <span>Si salva quando esci dal campo o con Ctrl+Invio.</span>
        <span aria-live="polite">{saved === "saving" ? "Salvataggio…" : saved === "saved" ? "Nota salvata" : ""}</span>
      </p>
    </div>
  );
}

function RejectSummary({ posting, context }: { posting: PostingSummary; context?: string | null }) {
  const hits = posting.filter_hits.filter((h) => h.action === "reject");
  if (hits.length === 0) return null;
  return (
    <ul className="space-y-2">
      {hits.map((h, i) => (
        <li key={i} className="rounded-lg border border-danger/20 bg-danger-soft/40 p-3 text-sm">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Badge tone={h.overridden ? "neutral" : "danger"} icon={h.overridden ? "undo" : "x"}>
              {h.overridden ? "Scarto annullato" : "Scartato"}: {ruleLabel(h.rule_id)}
            </Badge>
            <code className="font-mono text-xs text-ink-muted">{h.rule_id}</code>
          </p>
          <p className="mt-1.5">{h.reason}</p>
          {h.evidence && (
            <p className="mt-1.5 text-ink-muted">
              <EvidenceSnippet evidence={h.evidence} text={context} />
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

export function PostingRow({
  posting,
  defaultOpen = false,
  open: openProp,
  onToggle,
  active = false,
  position,
}: {
  posting: PostingSummary;
  defaultOpen?: boolean;
  /** Controlled expansion (keyboard navigation); uncontrolled when omitted. */
  open?: boolean;
  onToggle?: () => void;
  /** Current row of the keyboard cursor. */
  active?: boolean;
  position?: { index: number; total: number };
}) {
  const [openState, setOpenState] = useState(defaultOpen);
  const open = openProp ?? openState;
  const toggle = onToggle ?? (() => setOpenState((o) => !o));
  const panelId = useId();
  const rowRef = useRef<HTMLLIElement>(null);
  const patch = usePatchPosting();
  const actions = usePostingActions();
  const generate = useGeneratePack();
  const detail = usePosting(open && hasEvidence(posting) ? posting.id : NaN);
  const navigate = useNavigate();
  const toast = useToast();
  const p = posting;
  const rejected = p.filter_state === "rejected";
  const flagged = p.filter_state === "flagged" || p.filter_hits.some((h) => h.action === "flag" && !h.overridden);
  const skipReason = skipReasonOf(p);
  const context = detail.data?.raw_text;

  useEffect(() => {
    if (defaultOpen) rowRef.current?.scrollIntoView({ block: "center" });
  }, [defaultOpen]);

  function setStatus(status: PostingSummary["status"], msg: string) {
    patch.mutateAsync({ id: p.id, patch: { status } }).then(
      () => toast.success(msg),
      (e: Error) => toast.error(`Operazione non riuscita. ${e.message}`),
    );
  }

  return (
    <li
      ref={rowRef}
      data-posting-id={p.id}
      className={`card overflow-hidden ${rejected ? "border-dashed bg-surface/70" : ""} ${active ? "ring-2 ring-accent/60 ring-offset-1 ring-offset-canvas" : ""}`}
    >
      <h3 className="m-0">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          data-row-toggle={p.id}
          onClick={toggle}
          className="flex w-full items-start gap-3 rounded-xl p-4 text-left hover:bg-subtle/60 sm:items-center"
        >
          <span className="mt-0.5 shrink-0 sm:mt-0 sm:w-24">
            <PriorityBadge priority={p.priority} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5">
              {flagged && (
                <span className="text-warning-ink" title="Da verificare">
                  <Icon name="alert" size={14} />
                  <span className="sr-only">Attenzione: ci sono segnalazioni.</span>
                </span>
              )}
              <span className={`truncate font-medium ${p.status === "closed" ? "text-ink-muted line-through decoration-ink-muted/50" : ""}`}>
                {p.title}
              </span>
            </span>
            <span className="mt-0.5 flex flex-wrap gap-y-0.5 text-[13px] font-normal text-ink-muted [&>*+*]:before:mx-1.5 [&>*+*]:before:content-['·']">
              <span className="font-medium text-ink">{p.company_name}</span>
              {p.location && <span>{p.location}</span>}
              <span>
                <Deadline value={p.deadline} />
              </span>
              <SourceChips posting={p} />
            </span>
            <span className="mt-1.5 flex flex-wrap gap-1 empty:hidden">
              <FilterStateBadge state={p.filter_state} />
              <UserStatusBadge status={p.user_status} reason={skipReason} />
              <PostingStatusBadge status={p.status} />
              {p.track === "post_graduation" && (
                <Badge tone="info" icon="briefcase">
                  Dopo la laurea
                </Badge>
              )}
              {p.has_pack && (
                <Badge tone="accent" icon="file">
                  Pacchetto pronto
                </Badge>
              )}
            </span>
          </span>
          <Icon name="chevronDown" size={18} className={`mt-1 shrink-0 text-ink-muted transition-transform sm:mt-0 ${open ? "rotate-180" : ""}`} />
          <span className="sr-only">
            {open ? "Comprimi dettagli" : "Espandi dettagli"}
            {position && `, annuncio ${position.index + 1} di ${position.total}`}
          </span>
        </button>
      </h3>

      {rejected && (
        <div className="space-y-3 border-t border-line px-4 pb-4 pt-3">
          <RejectSummary posting={p} context={context} />
          <button type="button" className="btn-secondary" aria-keyshortcuts="r" onClick={() => actions.restore(p)}>
            <Icon name="undo" size={14} /> Ripristina
          </button>
        </div>
      )}

      <div id={panelId} hidden={!open} className="border-t border-line px-4 pb-5 pt-4">
        {open && (
          <div className="space-y-5">
            {p.score ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <h4 className="eyebrow mb-1">Perché è in linea</h4>
                  <p className="text-sm leading-relaxed">{p.score.why_fits || "—"}</p>
                </div>
                <div>
                  <h4 className="eyebrow mb-1">Cosa non torna</h4>
                  <p className="text-sm leading-relaxed">{p.score.what_doesnt_fit || "—"}</p>
                </div>
                <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm md:col-span-2">
                  {p.score.start_window && (
                    <div className="flex gap-1">
                      <dt className="text-ink-muted">Finestra di inizio:</dt>
                      <dd>{p.score.start_window}</dd>
                    </div>
                  )}
                  {p.score.language_requirements.length > 0 && (
                    <div className="flex gap-1">
                      <dt className="text-ink-muted">Lingue:</dt>
                      <dd>{p.score.language_requirements.join(", ")}</dd>
                    </div>
                  )}
                  {p.type && (
                    <div className="flex gap-1">
                      <dt className="text-ink-muted">Tipo:</dt>
                      <dd>{p.type}</dd>
                    </div>
                  )}
                  <div className="flex gap-1">
                    <dt className="text-ink-muted">Visto la prima volta:</dt>
                    <dd>{formatDate(p.first_seen)}</dd>
                  </div>
                </dl>
              </div>
            ) : (
              <p className="text-sm text-ink-muted">
                {rejected ? "Annuncio scartato dai filtri prima della valutazione." : "Non ancora valutato: verrà analizzato alla prossima esecuzione."}
              </p>
            )}

            {p.score && p.score.warnings.length > 0 && (
              <div className="rounded-lg border border-warning-ink/20 bg-warning-soft p-3 text-sm text-warning-ink">
                <h4 className="flex items-center gap-1.5 font-semibold">
                  <Icon name="alert" size={14} /> Avvisi
                </h4>
                <ul className="mt-1 list-disc pl-5">
                  {p.score.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {p.filter_hits.some((h) => !rejected || h.action !== "reject") && (
              <div>
                <h4 className="eyebrow mb-2">Regole dei filtri</h4>
                <ul className="space-y-2">
                  {p.filter_hits
                    .filter((h) => !rejected || h.action !== "reject")
                    .map((h, i) => (
                      <li key={i} className="rounded-lg border border-line bg-canvas p-3 text-sm">
                        <div className="flex flex-wrap items-center gap-2">
                          {h.action === "reject" ? (
                            <Badge tone={h.overridden ? "neutral" : "danger"} icon={h.overridden ? "undo" : "x"}>
                              {h.overridden ? "Scarto annullato" : "Scarto"}
                            </Badge>
                          ) : (
                            <Badge tone="warning" icon="flag">
                              Segnalazione
                            </Badge>
                          )}
                          <span className="font-medium">{ruleLabel(h.rule_id)}</span>
                          <code className="font-mono text-xs text-ink-muted">{h.rule_id}</code>
                        </div>
                        <p className="mt-1.5">{h.reason}</p>
                        {h.evidence && (
                          <p className="mt-1.5 text-ink-muted">
                            <EvidenceSnippet evidence={h.evidence} text={context} />
                          </p>
                        )}
                      </li>
                    ))}
                </ul>
              </div>
            )}

            <SourcesLine posting={p} />

            <div className="flex flex-wrap gap-2">
              <ExternalLink href={p.url} className="btn-secondary">
                Apri annuncio
              </ExternalLink>
              {p.has_pack ? (
                <Link to={`/annunci/${p.id}/pacchetto`} className="btn-primary" aria-keyshortcuts="p">
                  <Icon name="file" size={14} /> Pacchetto candidatura
                </Link>
              ) : (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={generate.isPending || rejected}
                  onClick={() =>
                    generate.mutate(p.id, {
                      onSuccess: () => {
                        toast.success("Pacchetto generato: rivedilo prima di usarlo.");
                        navigate(`/annunci/${p.id}/pacchetto`);
                      },
                      onError: (e) => toast.error(`Generazione non riuscita. ${e.message}`),
                    })
                  }
                >
                  <Icon name="sparkles" size={14} />
                  {generate.isPending ? "Generazione in corso…" : "Genera pacchetto"}
                </button>
              )}
              {p.user_status !== "sent" && (
                <button type="button" className="btn-secondary" aria-keyshortcuts="s" onClick={() => actions.markSent(p)}>
                  <Icon name="check" size={14} /> Segna come inviata
                </button>
              )}
              {p.user_status === "skipped" ? (
                <button type="button" className="btn-ghost" onClick={() => actions.unskip(p)}>
                  <Icon name="undo" size={14} /> Annulla salto
                </button>
              ) : (
                p.user_status === "todo" && (
                  <button type="button" className="btn-ghost" aria-keyshortcuts="x" onClick={() => actions.skip(p)}>
                    <Icon name="skip" size={14} /> Salta…
                  </button>
                )
              )}
              {p.status === "closed" ? (
                <button type="button" className="btn-ghost" onClick={() => setStatus("open", "Annuncio segnato come aperto.")}>
                  Segna aperto
                </button>
              ) : (
                <button type="button" className="btn-ghost" onClick={() => setStatus("closed", "Annuncio segnato come chiuso.")}>
                  <Icon name="lock" size={14} /> Segna chiuso
                </button>
              )}
            </div>

            <NoteField posting={p} />
          </div>
        )}
      </div>
    </li>
  );
}

const hasEvidence = (p: PostingSummary) => p.filter_hits.some((h) => !!h.evidence);
