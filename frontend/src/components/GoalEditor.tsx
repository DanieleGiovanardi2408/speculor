import { useId, useState, type FormEvent } from "react";
import { usePutSettings } from "../api/hooks";
import { useToast } from "./Toast";

/** Inline weekly-goal editor (PUT /api/settings). */
export function GoalEditor({ goal }: { goal: number }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(goal));
  const inputId = useId();
  const put = usePutSettings();
  const toast = useToast();

  function submit(e: FormEvent) {
    e.preventDefault();
    const n = Number(value);
    if (!Number.isInteger(n) || n < 1 || n > 100) {
      toast.error("Inserisci un numero intero tra 1 e 100.");
      return;
    }
    setEditing(false);
    put.mutate(
      { weekly_goal: n },
      {
        onSuccess: () => toast.success(`Obiettivo settimanale aggiornato a ${n}.`),
        onError: (err) => toast.error(`Obiettivo non salvato: ${err.message}`),
      },
    );
  }

  if (!editing)
    return (
      <button
        type="button"
        className="btn-ghost btn-sm"
        onClick={() => {
          setValue(String(goal));
          setEditing(true);
        }}
      >
        Modifica obiettivo
      </button>
    );

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <div>
        <label htmlFor={inputId} className="label">
          Obiettivo settimanale
        </label>
        <input
          id={inputId}
          type="number"
          min={1}
          max={100}
          inputMode="numeric"
          className="field w-24"
          value={value}
          // eslint-disable-next-line jsx-a11y/no-autofocus -- user just asked to edit this field
          autoFocus
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
        />
      </div>
      <button type="submit" className="btn-primary btn-sm">
        Salva
      </button>
      <button type="button" className="btn-ghost btn-sm" onClick={() => setEditing(false)}>
        Annulla
      </button>
    </form>
  );
}

export function WeeklyProgress({ done, goal }: { done: number; goal: number }) {
  const pct = goal > 0 ? Math.min(100, Math.round((done / goal) * 100)) : 0;
  const reached = done >= goal;
  return (
    <div>
      <p className="flex items-baseline gap-2">
        <span className="text-4xl font-semibold tabular-nums tracking-tight">
          {done} / {goal}
        </span>
        <span className="text-sm text-ink-muted">candidature questa settimana</span>
      </p>
      <div
        role="progressbar"
        aria-label="Candidature inviate rispetto all'obiettivo settimanale"
        aria-valuemin={0}
        aria-valuemax={goal}
        aria-valuenow={Math.min(done, goal)}
        aria-valuetext={`${done} su ${goal}`}
        className="mt-3 h-2.5 w-full overflow-hidden rounded-full bg-subtle"
      >
        <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-2 text-sm text-ink-muted">
        {reached ? "Obiettivo raggiunto: ottimo lavoro." : `Ne mancano ${goal - done} per raggiungere l'obiettivo.`}
      </p>
    </div>
  );
}
