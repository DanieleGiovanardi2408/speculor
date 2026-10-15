import { useId, useState, type FormEvent } from "react";
import { useCreateContact, useDeleteContact, useGenerateDraftForContact } from "../api/hooks";
import type { Contact, Draft, DraftKind } from "../api/types";
import { DRAFT_KIND_LABEL } from "../lib/labels";
import { DraftCard } from "./DraftCard";
import { ExternalLink } from "./ExternalLink";
import { Icon } from "./Icon";
import { useToast } from "./Toast";

function AddContactForm({ postingId }: { postingId: number }) {
  const ids = { name: useId(), role: useId(), url: useId(), why: useId() };
  const empty = { name: "", role: "", profile_url: "", why: "" };
  const [form, setForm] = useState(empty);
  const [error, setError] = useState<string | null>(null);
  const create = useCreateContact(postingId);
  const toast = useToast();

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      setError("Il nome è obbligatorio.");
      document.getElementById(ids.name)?.focus();
      return;
    }
    if (form.profile_url && !/^https?:\/\//.test(form.profile_url.trim())) {
      setError("L'URL del profilo deve iniziare con https://");
      document.getElementById(ids.url)?.focus();
      return;
    }
    create.mutate(
      {
        posting_id: postingId,
        name: form.name.trim(),
        role: form.role.trim() || undefined,
        profile_url: form.profile_url.trim() || undefined,
        why: form.why.trim() || undefined,
      },
      {
        onSuccess: (c) => {
          toast.success(`Contatto aggiunto: ${c.name}`);
          setForm(empty);
          setError(null);
        },
        onError: (err) => setError(err.message),
      },
    );
  }

  const set = (k: keyof typeof empty) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setForm((f) => ({ ...f, [k]: e.target.value }));
    setError(null);
  };

  return (
    <form onSubmit={submit} noValidate className="rounded-xl border border-dashed border-line-strong p-4" aria-labelledby={`${ids.name}-legend`}>
      <h3 id={`${ids.name}-legend`} className="mb-3 text-sm font-semibold">
        Aggiungi un contatto
      </h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={ids.name} className="label">
            Nome <span aria-hidden="true" className="text-danger-ink">*</span>
          </label>
          <input id={ids.name} className="field" value={form.name} onChange={set("name")} required aria-required="true" autoComplete="off" />
        </div>
        <div>
          <label htmlFor={ids.role} className="label">
            Ruolo
          </label>
          <input id={ids.role} className="field" value={form.role} onChange={set("role")} placeholder="es. Talent Acquisition" />
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={ids.url} className="label">
            URL profilo pubblico
          </label>
          <input id={ids.url} type="url" inputMode="url" className="field" value={form.profile_url} onChange={set("profile_url")} placeholder="https://www.linkedin.com/in/…" />
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={ids.why} className="label">
            Perché contattarla
          </label>
          <textarea id={ids.why} className="field" rows={2} value={form.why} onChange={set("why")} placeholder="es. guida il team citato nell'annuncio" />
        </div>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger-ink">
          {error}
        </p>
      )}
      <button type="submit" className="btn-secondary mt-3" disabled={create.isPending}>
        <Icon name="plus" size={14} /> {create.isPending ? "Aggiunta…" : "Aggiungi contatto"}
      </button>
    </form>
  );
}

function ContactItem({ contact, drafts, postingId }: { contact: Contact; drafts: Draft[]; postingId: number }) {
  const kindId = useId();
  const [kind, setKind] = useState<DraftKind>(contact.profile_url ? "linkedin_connection_note" : "email");
  const del = useDeleteContact(postingId);
  const gen = useGenerateDraftForContact();
  const toast = useToast();
  return (
    <li className="card p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 font-medium">
            <Icon name="user" className="text-ink-muted" />
            {contact.name}
            {contact.role && <span className="font-normal text-ink-muted">· {contact.role}</span>}
          </p>
          {contact.why && <p className="mt-1 text-sm text-ink-muted">{contact.why}</p>}
          {contact.profile_url && (
            <p className="mt-1 text-sm">
              <ExternalLink href={contact.profile_url}>Profilo pubblico</ExternalLink>
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={kindId} className="sr-only">
            Tipo di messaggio per {contact.name}
          </label>
          <select id={kindId} className="field btn-sm w-auto" value={kind} onChange={(e) => setKind(e.target.value as DraftKind)}>
            {(Object.keys(DRAFT_KIND_LABEL) as DraftKind[]).map((k) => (
              <option key={k} value={k}>
                {DRAFT_KIND_LABEL[k]}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn-secondary btn-sm"
            disabled={gen.isPending}
            onClick={() =>
              gen.mutate(
                { posting_id: postingId, contact_id: contact.id, kind },
                {
                  onSuccess: () => toast.success(`Bozza creata per ${contact.name}. La trovi qui sotto e in Approvazioni.`),
                  onError: (e) => toast.error(`Generazione non riuscita: ${e.message}`),
                },
              )
            }
          >
            <Icon name="sparkles" size={14} />
            {gen.isPending ? "Generazione…" : "Genera messaggio per questo contatto"}
          </button>
          <button
            type="button"
            className="btn-danger btn-sm"
            aria-label={`Elimina contatto ${contact.name}`}
            onClick={() =>
              // mutateAsync: the item unmounts optimistically before the request settles.
              del.mutateAsync(contact.id).then(
                () => toast.success(`Contatto eliminato: ${contact.name}`),
                (e: Error) => toast.error(`Eliminazione non riuscita: ${e.message}`),
              )
            }
          >
            <Icon name="trash" size={14} /> Elimina
          </button>
        </div>
      </div>
      {drafts.length > 0 && (
        <div className="mt-4 space-y-3">
          {drafts.map((d) => (
            <DraftCard key={d.id} draft={d} />
          ))}
        </div>
      )}
    </li>
  );
}

export function ContactsSection({ postingId, contacts, drafts }: { postingId: number; contacts: Contact[]; drafts: Draft[] }) {
  const orphan = drafts.filter((d) => d.contact_id == null || !contacts.some((c) => c.id === d.contact_id));
  return (
    <div className="space-y-4">
      {contacts.length === 0 ? (
        <p className="text-sm text-ink-muted">Nessun contatto ancora. Aggiungi una persona da cui farti conoscere: il messaggio lo approvi tu.</p>
      ) : (
        <ul className="space-y-3">
          {contacts.map((c) => (
            <ContactItem key={c.id} contact={c} postingId={postingId} drafts={drafts.filter((d) => d.contact_id === c.id)} />
          ))}
        </ul>
      )}
      {orphan.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold">Altri messaggi</h3>
          {orphan.map((d) => (
            <DraftCard key={d.id} draft={d} />
          ))}
        </div>
      )}
      <AddContactForm postingId={postingId} />
    </div>
  );
}
