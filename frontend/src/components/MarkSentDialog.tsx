import { useEffect, useId, useState, type FormEvent } from "react";
import { useApplications, useMarkPostingSent, usePosting } from "../api/hooks";
import { DEFAULT_CV, guessChannel } from "../lib/channel";
import { todayIso } from "../lib/format";
import { CHANNEL_OPTIONS } from "../lib/labels";
import { Dialog } from "./Dialog";
import { useToast } from "./Toast";

/** Known CV versions, derived from past applications (no dedicated endpoint in the contract). */
export function useCvVersions(extra: (string | null | undefined)[] = []): string[] {
  const { data } = useApplications();
  const set = new Set<string>();
  extra.forEach((v) => v && set.add(v));
  data?.forEach((a) => a.cv_version && set.add(a.cv_version));
  return [...set].sort();
}

export function MarkSentDialog({
  open,
  onClose,
  postingId,
  postingTitle,
  defaultCv,
}: {
  open: boolean;
  onClose: () => void;
  postingId: number;
  postingTitle: string;
  defaultCv?: string | null;
}) {
  // Detail carries `via` (how to apply); it is usually cached already.
  const detail = usePosting(open ? postingId : NaN);
  const ids = { channel: useId(), cv: useId(), date: useId(), list: useId() };
  const cvs = useCvVersions([defaultCv]);
  const [channel, setChannel] = useState("Sito aziendale");
  const [cv, setCv] = useState(defaultCv ?? "");
  const [date, setDate] = useState(todayIso());
  const [error, setError] = useState<string | null>(null);
  const mark = useMarkPostingSent();
  const toast = useToast();

  useEffect(() => {
    if (open) {
      setCv(defaultCv || DEFAULT_CV);
      setDate(todayIso());
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  useEffect(() => {
    if (open) setChannel(guessChannel(detail.data?.source, detail.data?.via));
  }, [open, detail.data?.source, detail.data?.via]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!cv.trim()) {
      setError("Indica la versione del CV usata.");
      return;
    }
    mark.mutateAsync({ id: postingId, body: { channel, cv_version: cv.trim(), date_sent: date } }).then(
      () => {
        toast.success(`Candidatura registrata (${channel}, ${cv.trim()}). La trovi in Candidature.`);
        onClose();
      },
      (err: Error) => setError(err.message),
    );
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Segna come inviata"
      description={`Registra che hai inviato tu la candidatura per “${postingTitle}”. Radar non invia nulla. Premi Invio per confermare.`}
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        <div>
          <label htmlFor={ids.channel} className="label">
            Canale
          </label>
          <select id={ids.channel} className="field" value={channel} onChange={(e) => setChannel(e.target.value)}>
            {CHANNEL_OPTIONS.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={ids.cv} className="label">
            Versione CV
          </label>
          <input
            id={ids.cv}
            className="field"
            list={ids.list}
            value={cv}
            onChange={(e) => setCv(e.target.value)}
            required
            aria-invalid={!!error && !cv.trim()}
            aria-describedby={error ? `${ids.cv}-err` : undefined}
            placeholder="es. cv-data-it-v3"
          />
          <datalist id={ids.list}>
            {cvs.map((v) => (
              <option key={v} value={v} />
            ))}
          </datalist>
        </div>
        <div>
          <label htmlFor={ids.date} className="label">
            Data di invio
          </label>
          <input id={ids.date} type="date" className="field" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} required />
        </div>
        {error && (
          <p id={`${ids.cv}-err`} role="alert" className="text-sm text-danger-ink">
            {error}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2 pt-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Annulla
          </button>
          <button type="submit" className="btn-primary" disabled={mark.isPending} data-autofocus>
            {mark.isPending ? "Salvataggio…" : "Segna come inviata"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
