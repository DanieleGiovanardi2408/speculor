import { useEffect, useId, useState, type FormEvent, type KeyboardEvent } from "react";
import { composeReason, SKIP_CHIPS } from "../lib/skip";
import { isTypingTarget } from "../lib/shortcuts";
import { Dialog } from "./Dialog";

/** "Salta" with a reason: quick chips (keys 1-6) + optional free text. Enter confirms. */
export function SkipDialog({
  open,
  onClose,
  postingTitle,
  onConfirm,
}: {
  open: boolean;
  onClose: () => void;
  postingTitle: string;
  onConfirm: (reason: string) => void;
}) {
  const ids = { group: useId(), detail: useId(), hint: useId() };
  const [chip, setChip] = useState<string>(SKIP_CHIPS[0]);
  const [detail, setDetail] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setChip(SKIP_CHIPS[0]);
      setDetail("");
      setError(null);
    }
  }, [open]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (chip === "Altro" && !detail.trim()) {
      setError("Scrivi in due parole il motivo.");
      return;
    }
    onConfirm(composeReason(chip, detail));
    onClose();
  }

  function onKeyDown(e: KeyboardEvent<HTMLFormElement>) {
    if (isTypingTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
    const n = Number(e.key);
    if (Number.isInteger(n) && n >= 1 && n <= SKIP_CHIPS.length) {
      e.preventDefault();
      setChip(SKIP_CHIPS[n - 1]!);
      setError(null);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Salta annuncio" description={`Perché salti “${postingTitle}”? Il motivo resta nella nota, così ricordi la scelta.`}>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- number keys pick a reason */}
      <form onSubmit={submit} onKeyDown={onKeyDown} className="space-y-4" noValidate>
        <fieldset aria-describedby={ids.hint}>
          <legend className="label">Motivo</legend>
          <div className="flex flex-wrap gap-2">
            {SKIP_CHIPS.map((c, i) => {
              const id = `${ids.group}-${i}`;
              return (
                <div key={c}>
                  <input
                    id={id}
                    type="radio"
                    name={ids.group}
                    value={c}
                    checked={chip === c}
                    onChange={() => {
                      setChip(c);
                      setError(null);
                    }}
                    className="peer sr-only"
                  />
                  <label
                    htmlFor={id}
                    className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-full border border-line-strong bg-surface px-3.5 text-sm font-medium text-ink peer-checked:border-accent peer-checked:bg-accent-soft peer-checked:text-accent-ink peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent sm:min-h-9"
                  >
                    <span aria-hidden="true" className="text-xs text-ink-muted">
                      {i + 1}
                    </span>
                    {c}
                    {chip === c && <span className="sr-only"> (selezionato)</span>}
                  </label>
                </div>
              );
            })}
          </div>
          <p id={ids.hint} className="mt-1.5 text-xs text-ink-muted">
            Scorciatoia: tasti da 1 a {SKIP_CHIPS.length}.
          </p>
        </fieldset>
        <div>
          <label htmlFor={ids.detail} className="label">
            Dettagli {chip === "Altro" ? "" : "(facoltativi)"}
          </label>
          <input
            id={ids.detail}
            className="field"
            value={detail}
            maxLength={150}
            onChange={(e) => {
              setDetail(e.target.value);
              setError(null);
            }}
            aria-invalid={!!error}
            aria-describedby={error ? `${ids.detail}-err` : undefined}
            placeholder={chip === "Sede" ? "es. solo in presenza a Londra" : "es. chiede 3 anni di esperienza"}
          />
          {error && (
            <p id={`${ids.detail}-err`} role="alert" className="mt-1 text-sm text-danger-ink">
              {error}
            </p>
          )}
        </div>
        <div className="flex flex-wrap justify-end gap-2 pt-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Annulla
          </button>
          <button type="submit" className="btn-primary" data-autofocus>
            Salta: {chip}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
