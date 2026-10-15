import { useEffect, useId, useState } from "react";
import { useMarkDraftSent, usePatchDraft } from "../api/hooks";
import type { Draft, DraftPatch } from "../api/types";
import { formatDateTime } from "../lib/format";
import { DRAFT_KIND_LABEL, DRAFT_LIMIT } from "../lib/labels";
import { DraftStatusBadge } from "./Badge";
import { CharCounter } from "./CharCounter";
import { CopyButton } from "./CopyButton";
import { Icon } from "./Icon";
import { useToast } from "./Toast";

export function UnsavedIndicator({ dirty }: { dirty: boolean }) {
  return (
    <span aria-live="polite" className="text-xs">
      {dirty && (
        <span className="inline-flex items-center gap-1 font-medium text-warning-ink">
          <span className="h-1.5 w-1.5 rounded-full bg-warning-ink" aria-hidden="true" />
          Modifiche non salvate
        </span>
      )}
    </span>
  );
}

/** Editable draft (status=draft) or read-only view (approved / sent / skipped). */
export function DraftCard({ draft, showPosting = false }: { draft: Draft; showPosting?: boolean }) {
  const ids = { text: useId(), rcpt: useId(), counter: useId(), rcptHelp: useId() };
  const [text, setText] = useState(draft.text);
  const [recipient, setRecipient] = useState(draft.recipient ?? "");
  const [rcptError, setRcptError] = useState(false);
  const patch = usePatchDraft();
  const markSent = useMarkDraftSent();
  const toast = useToast();

  useEffect(() => setText(draft.text), [draft.text]);
  useEffect(() => setRecipient(draft.recipient ?? ""), [draft.recipient]);

  const editable = draft.status === "draft";
  const limit = DRAFT_LIMIT[draft.kind];
  const over = limit != null && text.length > limit;
  const isLinkedin = draft.channel === "linkedin";
  const dirty = text !== draft.text || recipient !== (draft.recipient ?? "");

  function send(p: DraftPatch, msg: string) {
    // mutateAsync: the card can leave its list (status change) before the request settles.
    patch.mutateAsync({ id: draft.id, patch: p }).then(
      () => toast.success(msg),
      (e: Error) => toast.error(`Operazione non riuscita: ${e.message}`),
    );
  }

  function changes(): DraftPatch {
    const p: DraftPatch = {};
    if (text !== draft.text) p.text = text;
    if (recipient !== (draft.recipient ?? "")) p.recipient = recipient.trim() || null;
    return p;
  }

  function approve() {
    if (isLinkedin && !recipient.trim()) {
      setRcptError(true);
      document.getElementById(ids.rcpt)?.focus();
      return;
    }
    if (over) {
      toast.error(`Il testo supera il limite di ${limit} caratteri: accorcialo prima di approvare.`);
      return;
    }
    send({ ...changes(), status: "approved" }, "Messaggio approvato. Verrà inviato solo con radar send dal tuo computer.");
  }

  return (
    <article className="card p-4 sm:p-5" aria-labelledby={`${ids.text}-title`}>
      <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 id={`${ids.text}-title`} className="text-sm font-semibold">
            {DRAFT_KIND_LABEL[draft.kind]}
            {draft.contact_name && <span className="font-normal text-ink-muted"> · per {draft.contact_name}</span>}
          </h3>
          {showPosting && draft.posting_title && (
            <p className="text-xs text-ink-muted">
              {draft.posting_title}
              {draft.company_name && ` · ${draft.company_name}`}
            </p>
          )}
        </div>
        <DraftStatusBadge status={draft.status} />
      </header>

      {isLinkedin && editable && (
        <div className="mb-3">
          <label htmlFor={ids.rcpt} className="label">
            Destinatario LinkedIn (URL del profilo o ID pubblico)
            <span className="text-danger-ink" aria-hidden="true">
              {" "}
              *
            </span>
          </label>
          <input
            id={ids.rcpt}
            className="field"
            value={recipient}
            onChange={(e) => {
              setRecipient(e.target.value);
              setRcptError(false);
            }}
            placeholder="https://www.linkedin.com/in/… oppure nome-cognome-123"
            aria-required="true"
            aria-invalid={rcptError}
            aria-describedby={ids.rcptHelp}
          />
          <p id={ids.rcptHelp} className={`mt-1 text-xs ${rcptError ? "font-medium text-danger-ink" : "text-ink-muted"}`}>
            {rcptError && "Obbligatorio per approvare. "}
            Serve a <code className="font-mono">radar send</code> sul tuo computer per aprire la conversazione con la persona giusta.
          </p>
        </div>
      )}
      {!editable && draft.recipient && (
        <p className="mb-2 text-xs text-ink-muted">
          Destinatario: <span className="font-medium text-ink break-all">{draft.recipient}</span>
        </p>
      )}

      {editable ? (
        <div>
          <label htmlFor={ids.text} className="sr-only">
            Testo del messaggio
          </label>
          <textarea
            id={ids.text}
            className={`field min-h-28 leading-relaxed ${over ? "border-danger" : ""}`}
            rows={Math.min(12, Math.max(4, text.split("\n").reduce((n, line) => n + Math.max(1, Math.ceil(line.length / 60)), 0)))}
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-describedby={ids.counter}
            aria-invalid={over}
          />
        </div>
      ) : (
        <p className="whitespace-pre-wrap rounded-lg bg-canvas p-3 text-sm leading-relaxed">{draft.text}</p>
      )}

      <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2">
        <CharCounter id={ids.counter} count={text.length} max={limit} />
        {editable && <UnsavedIndicator dirty={dirty} />}
      </div>

      {draft.style_issues.length > 0 && (
        <ul className="mt-3 space-y-1 rounded-lg border border-warning-ink/20 bg-warning-soft p-3 text-sm text-warning-ink">
          {draft.style_issues.map((s, i) => (
            <li key={i} className="flex gap-1.5">
              <Icon name="alert" size={14} className="mt-0.5 shrink-0" />
              <span>
                <span className="sr-only">Avviso di stile: </span>
                {s}
              </span>
            </li>
          ))}
        </ul>
      )}

      {draft.status === "sent" && (
        <p className="mt-3 text-xs text-ink-muted">
          Inviato {formatDateTime(draft.sent_at)}
          {draft.send_result && ` · esito: ${draft.send_result}`}
        </p>
      )}
      {draft.status === "failed" && draft.send_result && (
        <p className="mt-3 text-sm text-danger-ink">Invio fallito: {draft.send_result}</p>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {editable && (
          <>
            <button type="button" className="btn-primary btn-sm" onClick={approve} disabled={patch.isPending}>
              <Icon name="check" size={14} /> Approva
            </button>
            <button
              type="button"
              className="btn-secondary btn-sm"
              disabled={!dirty || patch.isPending}
              onClick={() => send(changes(), "Bozza salvata.")}
            >
              Salva
            </button>
            <button type="button" className="btn-ghost btn-sm" onClick={() => send({ status: "skipped" }, "Bozza saltata.")}>
              <Icon name="skip" size={14} /> Salta
            </button>
          </>
        )}
        <CopyButton text={text} srContext={`testo: ${DRAFT_KIND_LABEL[draft.kind]}`} />
        {(draft.status === "approved" || draft.status === "failed") && (
          <>
            <button
              type="button"
              className="btn-secondary btn-sm"
              onClick={() =>
                markSent.mutateAsync(draft.id).then(
                  () => toast.success("Segnato come inviato a mano."),
                  (e: Error) => toast.error(`Operazione non riuscita: ${e.message}`),
                )
              }
            >
              <Icon name="send" size={14} /> Segna come inviata
            </button>
            <button type="button" className="btn-ghost btn-sm" onClick={() => send({ status: "draft" }, "Riportato in bozza.")}>
              <Icon name="undo" size={14} /> Riporta in bozza
            </button>
          </>
        )}
        {draft.status === "skipped" && (
          <button type="button" className="btn-ghost btn-sm" onClick={() => send({ status: "draft" }, "Riportato in bozza.")}>
            <Icon name="undo" size={14} /> Riporta in bozza
          </button>
        )}
      </div>
    </article>
  );
}
